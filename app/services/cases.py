"""案例：施工工序完工后自动生成对外展示记录。

只保存可公开字段（房号、工序、施工时间、师傅、产品类型），
绝不落客户姓名/手机/微信/合同金额/付款时间等隐私与商业信息。
"""
from __future__ import annotations

import io
import secrets
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Case, CaseImage, Contract, Customer, Task, User, now_iso, today_str


def product_type_for_customer(db: Session, customer: Customer) -> str | None:
    """对应合同的产品类型：取该客户最新一份（签订日期降序）填了产品类型的合同。"""
    if not customer:
        return None
    contracts = list(
        db.scalars(
            select(Contract)
            .where(Contract.customer_id == customer.id)
            .order_by(Contract.sign_date.desc(), Contract.id.desc())
        ).all()
    )
    for c in contracts:
        if c.product_type:
            return c.product_type
    return None


def _room_label(customer: Customer) -> str:
    """房号信息快照：楼盘名 + 楼栋-单元-房号（缺失段自动省略）。"""
    return (customer.room_label or "").strip() if customer else ""


def _new_token(db: Session) -> str:
    while True:
        token = secrets.token_urlsafe(16)
        if not db.scalars(select(Case).where(Case.share_token == token)).first():
            return token


def create_case_for_task(db: Session, task: Task) -> Case | None:
    """工序完工后生成案例（幂等：同一工序只生成一次）。"""
    from ..models import CASE_STAGES

    if not task or task.stage not in CASE_STAGES:
        return None
    existing = db.scalars(select(Case).where(Case.task_id == task.id)).first()
    if existing:
        return existing

    customer = db.get(Customer, task.customer_id)
    installer = db.get(User, task.assignee_id) if task.assignee_id else None

    case = Case(
        task_id=task.id,
        customer_id=task.customer_id,
        room_label=_room_label(customer) or "-",
        stage=task.stage,
        work_date=(task.actual_end or task.actual_start or today_str())[:10],
        installer_id=task.assignee_id,
        installer_name=(installer.display_name if installer else None),
        product_type=product_type_for_customer(db, customer),
        share_token=_new_token(db),
    )
    db.add(case)
    db.flush()
    return case


# ------------------------------------------------------------ 分享限制
SHARE_DURATIONS: dict[str, int] = {"1天": 1, "3天": 3, "1周": 7, "1月": 30}
SHARE_VIEW_LIMITS: dict[str, int] = {"10次": 10, "50次": 50, "200次": 200}
SHARE_UNLIMITED = "不限"


def share_expiry_from(duration: str, base: datetime | None = None) -> str | None:
    """把「1天/3天/1周/1月/不限」换算为绝对到期时间字符串；不限返回 None。"""
    days = SHARE_DURATIONS.get(duration)
    if not days:
        return None
    base = base or datetime.now()
    return (base + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def share_view_limit(limit: str) -> int | None:
    """把「10次/50次/200次/不限」换算为次数上限；不限返回 None。"""
    return SHARE_VIEW_LIMITS.get(limit)


def share_state(case: Case) -> tuple[bool, str]:
    """分享链接当前状态：返回 (是否有效, 说明)。满足任一条件即失效。"""
    if case.share_disabled:
        return False, "已被手动停用"
    if case.share_expires_at and now_iso() >= case.share_expires_at:
        return False, f"已于 {case.share_expires_at[:16]} 过期"
    if case.share_max_views and (case.share_views or 0) >= case.share_max_views:
        return False, "已达访问次数上限"
    return True, "有效"


def set_share_limits(db: Session, case: Case, duration: str, limit: str) -> None:
    """设置分享时间/次数限制；两者独立，空/不限即不限制。"""
    case.share_duration = duration if duration in SHARE_DURATIONS else SHARE_UNLIMITED
    case.share_expires_at = share_expiry_from(duration)
    case.share_max_views = share_view_limit(limit)
    # 改了限制后：次数从 0 重新计（语义更清晰）；失效开关保留由用户手动控制
    case.share_views = 0


# ------------------------------------------------------------ 案例展示图
def case_image_root() -> Path:
    root = get_settings().media_dir / "cases"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _make_thumb(img: Image.Image, dest: Path) -> None:
    s = get_settings()
    thumb = img.copy()
    thumb.thumbnail((s.thumb_max_edge, s.thumb_max_edge * 4), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if thumb.mode not in ("RGB", "RGBA"):
        thumb = thumb.convert("RGB")
    thumb.save(dest, "WEBP", quality=s.thumb_quality, method=4)


def save_case_image(db: Session, case: Case, raw: bytes, orig_name: str) -> tuple[CaseImage | None, str]:
    """保存一张案例展示图，返回 (CaseImage, 状态信息)。"""
    if not raw:
        return None, "空文件"
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as exc:  # pragma: no cover
        return None, f"无法解析图片：{exc}"

    ext = (Path(orig_name or "").suffix or ".jpg").lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}:
        ext = ".jpg"
    rel_dir = Path("cases") / str(case.id)
    fname = f"{uuid.uuid4().hex}{ext}"
    rel_path = rel_dir / fname
    root = case_image_root()
    dest = root / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)

    thumb_rel = rel_dir / f"{fname.rsplit('.', 1)[0]}_thumb.webp"
    try:
        _make_thumb(img, root / thumb_rel)
    except Exception:  # pragma: no cover
        thumb_rel = None

    sort_order = (len(db.scalars(select(CaseImage).where(CaseImage.case_id == case.id)).all()) or 0) + 1
    image = CaseImage(
        case_id=case.id,
        path=str(rel_path),
        thumb_path=str(thumb_rel) if thumb_rel else None,
        sort_order=sort_order,
        created_at=now_iso(),
    )
    db.add(image)
    return image, "已保存"


def delete_case_image(db: Session, image: CaseImage) -> str:
    root = case_image_root()
    db.delete(image)
    for rel in (image.path, image.thumb_path):
        if not rel:
            continue
        try:
            (root / rel).unlink(missing_ok=True)
        except OSError:  # pragma: no cover
            pass
    return "图片已删除"


def case_image_file(rel: str) -> Path:
    root = case_image_root().resolve()
    target = (root / rel).resolve()
    if not str(target).startswith(str(root)):
        raise ValueError("非法路径")
    return target


def images_by_case(db: Session, case_ids: list[int]) -> dict[int, list[CaseImage]]:
    ids = [i for i in case_ids if i]
    if not ids:
        return {}
    rows = list(
        db.scalars(
            select(CaseImage)
            .where(CaseImage.case_id.in_(ids))
            .order_by(CaseImage.sort_order, CaseImage.id)
        ).all()
    )
    grouped: dict[int, list[CaseImage]] = {}
    for img in rows:
        grouped.setdefault(img.case_id, []).append(img)
    return grouped
