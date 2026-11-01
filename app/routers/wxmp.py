"""公众号草稿同步路由：登录（扫码）、状态、发布。

发布是长任务（19 张图可能要几分钟），所以走后台线程 + 状态轮询，
不占用 uvicorn 的请求线程（单 worker 下同步跑会卡死整站）。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import log_action
from ..csrf import csrf_protect
from ..db import SessionLocal, commit_retry, get_db
from ..models import Case, CaseImage, now_iso
from ..permissions import require
from ..security import verify_csrf
from ..services import cases as cases_svc
from ..services import share_custom as custom_svc
from ..services import wxmp as wxmp_svc
from ..services import wxmp_body
from ..templating import csrf_cookie_name, redirect, render
from ..utils import client_ip, get_or_404

router = APIRouter(prefix="/wxmp", tags=["wxmp"])
log = logging.getLogger("crm.wxmp")


def _csrf_any(request: Request, csrf_token: str = Form("")) -> None:
    """校验 CSRF：优先读 `X-CSRF-Token` 头，其次退回表单字段。

    为什么不能直接用 csrf_protect：`/wxmp/cases/{id}` 和 `/wxmp/refresh` 是前端
    fetch 发出的、无 body 的 POST，带不了表单字段，只能走自定义头。
    """
    token = request.headers.get("X-CSRF-Token") or csrf_token
    if not verify_csrf(request.cookies.get(csrf_cookie_name()), token):
        raise HTTPException(status_code=403, detail="页面已过期或来源不可信，请刷新后重试")

# 进程内任务状态（单 worker 部署，够用；多 worker 需换共享存储）
_TASKS: dict[int, dict] = {}
_TASK_LOCK = threading.Lock()

# 单次同步的僵死阈值（分钟）。超过即视为后台线程已卡死、允许抢占重发。
# 依据：最坏情况 19 张图 × 90s 超时 ≈ 28.5 分钟，再留余量。
STALE_TASK_MINUTES = 45


def _task_of(case_id: int) -> dict | None:
    with _TASK_LOCK:
        return _TASKS.get(case_id)


def _is_stale_task(task: dict) -> bool:
    """进程内任务是否已僵死（running 但开始时间超过阈值）。

    后台线程可能卡在 CDP 调用上不返回（renderer 僵死、网络挂起），此时
    `_TASKS[case_id]` 会永远保持 running，防重会让用户永久 409 无法重试。
    超过阈值就当成僵死，允许抢占。
    """
    if task.get("status") != "running":
        return False
    started = task.get("started") or ""
    try:
        began = datetime.strptime(started, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return True  # 时间戳坏了也当作僵死，避免永久卡住
    return (datetime.now() - began).total_seconds() > STALE_TASK_MINUTES * 60


@router.get("")
def wxmp_page(request: Request, db: Session = Depends(get_db), user=Depends(require("case:view"))):
    available = wxmp_svc.is_available()
    state: dict = {"ok": False, "reason": "no_browser"}
    if available:
        try:
            state = wxmp_svc.login_state()
        except Exception as e:  # 浏览器启动失败不该把整页搞挂
            state = {"ok": False, "error": str(e)}

    # 最近同步过的案例，用来给用户核对
    recent = list(
        db.scalars(
            select(Case)
            .where(Case.wx_draft_status != "none")
            .where(Case.wx_draft_status.is_not(None))
            .order_by(Case.wx_draft_at.desc(), Case.id.desc())
            .limit(12)
        ).all()
    )
    return render(
        request,
        "wxmp/index.html",
        available=available,
        state=state,
        recent=recent,
        running=[
            t for t in _TASKS.values()
            if t.get("status") == "running" and not _is_stale_task(t)
        ],
        template=wxmp_svc.get_template(db),
        presets=wxmp_body.PRESETS,
        fields=wxmp_body.FIELDS,
    )


@router.post("/template")
def wxmp_save_template(
    request: Request,
    preset: str = Form("card"),
    brand: str = Form(""),
    lead: str = Form(""),
    tail: str = Form(""),
    accent: str = Form("#6c7f91"),
    font_size: int = Form(15),
    line_height: float = Form(1.9),
    show_site: str = Form(""),
    show_date: str = Form(""),
    show_stage: str = Form(""),
    show_product: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require("case:edit")),
    _csrf: None = Depends(csrf_protect),
):
    """保存正文模板。字段勾选用 checkbox（未勾选则不出现在请求里）。"""
    t = wxmp_svc.save_template(
        db,
        {
            "preset": preset,
            "brand": brand,
            "lead": lead,
            "tail": tail,
            "accent": accent,
            "font_size": font_size,
            "line_height": line_height,
            "show": {
                "site": bool(show_site),
                "date": bool(show_date),
                "stage": bool(show_stage),
                "product": bool(show_product),
            },
        },
    )
    commit_retry(db)
    # commit=True：log_action 默认只 add() 不提交，而请求结束 get_db() 只 close 不 commit
    # → 不显式提交的话这条审计会被静默丢弃（曾导致公众号发布动作零痕迹）。
    log_action(
        db, user, "wxmp_template_save", "app_settings", None,
        ip=client_ip(request), commit=True,
    )
    return redirect("/wxmp#template", "模板已保存", "ok")


@router.get("/preview")
def wxmp_preview(
    request: Request,
    case_id: int = 0,
    db: Session = Depends(get_db),
    user=Depends(require("case:view")),
):
    """正文预览：用真实案例（或示例数据）渲染当前模板，前端直接内嵌展示。

    ⚠️ 预览走 body_html()（富样式），而真实发布走 body_lines()（纯文本 + 逐行
    insertText）。微信编辑器会剥掉程序注入的字号/颜色/粗体，所以**预览 ≠ 终稿**，
    预览只是给用户看「字段开关/文案」效果，不代表草稿箱里的最终样式。
    """
    if case_id:
        case = db.get(Case, case_id)
    else:
        case = db.scalars(select(Case).order_by(Case.id.desc())).first()
    if case is None:
        return JSONResponse({"html": "<p style='color:#999'>暂无案例可预览</p>"})
    html = wxmp_body.body_html(case, wxmp_svc.get_template(db))
    return JSONResponse({"html": html, "title": wxmp_body.title_of("", case)})


@router.get("/qr.png")
def wxmp_qr(request: Request, user=Depends(require("case:view"))):
    p = wxmp_svc.qr_path()
    if not p.exists():
        return JSONResponse({"detail": "还没有二维码，请点刷新"}, status_code=404)
    return FileResponse(p, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/refresh")
def wxmp_refresh(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require("case:view")),
    _csrf: None = Depends(_csrf_any),
):
    """重新拉一次登录状态（新二维码 / 确认已登录）。"""
    try:
        state = wxmp_svc.login_state()
    except Exception as e:
        state = {"ok": False, "error": str(e)}
    return JSONResponse(state)


@router.post("/cases/{case_id}")
def wxmp_publish(
    request: Request,
    case_id: int,
    db: Session = Depends(get_db),
    user=Depends(require("case:edit")),
    _csrf: None = Depends(_csrf_any),
):
    case = get_or_404(db, Case, case_id, "案例")
    if not wxmp_svc.is_available():
        return JSONResponse({"ok": False, "error": "容器内没有 Chromium，该功能未启用"}, status_code=503)

    t = _task_of(case_id)
    if t and t.get("status") == "running":
        if not _is_stale_task(t):
            return JSONResponse({"ok": False, "error": "该案例正在同步中"}, status_code=409)
        # 超时视为后台线程已僵死，允许抢占重发（否则用户会永久卡在 409）
        log.warning(
            "案例 %s 上次同步已超时（started=%s，阈值 %d 分钟），视为僵死，允许重新发布",
            case_id, t.get("started"), STALE_TASK_MINUTES,
        )

    case.wx_draft_status = "running"
    case.wx_draft_note = ""
    commit_retry(db)
    # commit=True：同上，不显式提交会被 get_db() 的 close 丢弃，导致发布零审计。
    log_action(
        db, user, "wxmp_publish", "cases", case.id,
        ip=client_ip(request), commit=True,
    )

    with _TASK_LOCK:
        _TASKS[case_id] = {"status": "running", "started": now_iso(), "user": user.display_name}

    threading.Thread(target=_run_publish, args=(case_id,), daemon=True).start()
    return JSONResponse({"ok": True, "status": "running"})


def _run_publish(case_id: int) -> None:
    """后台线程：自己开 session（SQLAlchemy session 不能跨线程共用）。"""
    db = SessionLocal()
    result: dict = {"ok": False, "error": "未知错误"}
    try:
        case = db.get(Case, case_id)
        if case is None:
            result = {"ok": False, "error": "案例不存在"}
            return
        images = list(
            db.scalars(
                select(CaseImage)
                .where(CaseImage.case_id == case.id)
                .order_by(CaseImage.sort_order, CaseImage.id)
            ).all()
        )
        files: list[str] = []
        missing_on_disk: list[int] = []
        for img in images:
            try:
                p = cases_svc.case_image_file(img.path)
            except ValueError:
                missing_on_disk.append(img.id)
                continue
            if p.exists():
                files.append(str(p))
            else:
                missing_on_disk.append(img.id)

        # P1-1b：磁盘缺图必须先拦下。旧实现把缺图静默 continue，且 missing 用
        # 「过滤后」的 len 计算 → 磁盘缺多少都不计入 missing，前端永远显示 0，
        # 可能发出一篇零图/缺图草稿却毫无告警。
        if missing_on_disk:
            result = {
                "ok": False,
                "error": (
                    f"图片文件缺失：数据库登记 {len(images)} 条，磁盘只找到 {len(files)} 张"
                    f"（缺 id={missing_on_disk[:8]}{'…' if len(missing_on_disk) > 8 else ''}）。"
                    "请先恢复图片文件再同步，避免发出缺图草稿。"
                ),
            }
            case.wx_draft_status = "failed"
            case.wx_draft_note = result["error"][:500]
            commit_retry(db)
            return

        cfg = custom_svc.get_all(db)
        title = wxmp_body.title_of(wxmp_body.og_title(case, cfg), case)
        lines = wxmp_body.body_lines(case, wxmp_svc.get_template(db))

        result = wxmp_svc.publish_case(db, case, files, lines, title)

        case.wx_draft_status = "done" if result.get("ok") else "failed"
        if result.get("ok"):
            case.wx_draft_at = now_iso()
            case.wx_draft_note = result.get("warn") or ""
        else:
            case.wx_draft_note = (result.get("error") or "发布失败")[:500]
        commit_retry(db)
    except Exception as e:
        result = {"ok": False, "error": str(e)}
        try:
            case = db.get(Case, case_id)
            if case:
                case.wx_draft_status = "failed"
                case.wx_draft_note = str(e)[:500]
                commit_retry(db)
        except Exception:
            pass
    finally:
        with _TASK_LOCK:
            _TASKS[case_id] = {"status": "done" if result.get("ok") else "failed", "result": result}
        db.close()


@router.get("/status/{case_id}")
def wxmp_status(request: Request, case_id: int, db: Session = Depends(get_db), user=Depends(require("case:view"))):
    case = get_or_404(db, Case, case_id, "案例")
    t = _task_of(case_id) or {}

    # 僵死自愈：后台线程卡死时 DB 会永远停在 running（前端刷新后仍显示「进行中」）。
    # 这里顺手复位成 failed，让用户看到「失败·可重试」。只写一次（复位后不再是 running）。
    if t.get("status") == "running" and _is_stale_task(t):
        case.wx_draft_status = "failed"
        case.wx_draft_note = f"同步超时（超过 {STALE_TASK_MINUTES} 分钟无响应），可重新同步"
        commit_retry(db)
        log.warning("案例 %s 同步僵死（started=%s），已在状态查询时复位为 failed", case_id, t.get("started"))

    return JSONResponse(
        {
            "status": case.wx_draft_status or "none",
            "at": case.wx_draft_at or "",
            "note": case.wx_draft_note or "",
            "task": t.get("status", ""),
            "result": t.get("result", {}),
        }
    )
