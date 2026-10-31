"""公众号草稿同步路由：登录（扫码）、状态、发布。

发布是长任务（19 张图可能要几分钟），所以走后台线程 + 状态轮询，
不占用 uvicorn 的请求线程（单 worker 下同步跑会卡死整站）。
"""
from __future__ import annotations

import threading

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import log_action
from ..db import SessionLocal, commit_retry, get_db
from ..models import Case, CaseImage, now_iso
from ..permissions import require
from ..services import cases as cases_svc
from ..services import share_custom as custom_svc
from ..services import wxmp as wxmp_svc
from ..services import wxmp_body
from ..templating import redirect, render
from ..utils import client_ip, get_or_404

router = APIRouter(prefix="/wxmp", tags=["wxmp"])

# 进程内任务状态（单 worker 部署，够用；多 worker 需换共享存储）
_TASKS: dict[int, dict] = {}
_TASK_LOCK = threading.Lock()


def _task_of(case_id: int) -> dict | None:
    with _TASK_LOCK:
        return _TASKS.get(case_id)


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
        running=[t for t in _TASKS.values() if t.get("status") == "running"],
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
    log_action(db, user, "wxmp_template_save", "app_settings", None, ip=client_ip(request))
    return redirect("/wxmp#template", "模板已保存", "ok")


@router.get("/preview")
def wxmp_preview(
    request: Request,
    case_id: int = 0,
    db: Session = Depends(get_db),
    user=Depends(require("case:view")),
):
    """正文预览：用真实案例（或示例数据）渲染当前模板，前端直接内嵌展示。

    只返回 HTML 片段，不走完整页面——这样样式和最终塞进微信的那份完全一致。
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
def wxmp_refresh(request: Request, db: Session = Depends(get_db), user=Depends(require("case:view"))):
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
):
    case = get_or_404(db, Case, case_id, "案例")
    if not wxmp_svc.is_available():
        return JSONResponse({"ok": False, "error": "容器内没有 Chromium，该功能未启用"}, status_code=503)

    t = _task_of(case_id)
    if t and t.get("status") == "running":
        return JSONResponse({"ok": False, "error": "该案例正在同步中"}, status_code=409)

    case.wx_draft_status = "running"
    case.wx_draft_note = ""
    commit_retry(db)
    log_action(db, user, "wxmp_publish", "cases", case.id, ip=client_ip(request))

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
        for i, img in enumerate(images, start=1):
            try:
                p = cases_svc.case_image_file(img.path)
            except ValueError:
                continue
            if p.exists():
                files.append(str(p))

        cfg = custom_svc.get_all(db)
        title = wxmp_body.title_of(wxmp_body.og_title(case, cfg), case)
        html = wxmp_body.body_html(case, wxmp_svc.get_template(db))

        result = wxmp_svc.publish_case(db, case, files, html, title)

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
    return JSONResponse(
        {
            "status": case.wx_draft_status or "none",
            "at": case.wx_draft_at or "",
            "note": case.wx_draft_note or "",
            "task": t.get("status", ""),
            "result": t.get("result", {}),
        }
    )
