"""照片存储：路径规范、EXIF、WebP 缩略图、Hash 去重。"""
from __future__ import annotations

import hashlib
import io
import logging
import uuid
from datetime import datetime
from pathlib import Path

from PIL import ExifTags, Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Photo, now_iso

log = logging.getLogger("crm.photos")

CHUNK = 1024 * 1024


def media_root() -> Path:
    root = get_settings().media_dir
    root.mkdir(parents=True, exist_ok=True)
    return root


def _sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_exif(img: Image.Image) -> tuple[str | None, float | None, float | None]:
    taken, lat, lon = None, None, None
    try:
        exif = img.getexif()
        if not exif:
            return None, None, None
        mapping = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
        raw = mapping.get("DateTimeOriginal") or mapping.get("DateTime")
        if raw:
            try:
                taken = datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S").strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                taken = None
        gps = mapping.get("GPSInfo")
        if gps:
            gpsmap = {ExifTags.GPSTAGS.get(k, k): v for k, v in gps.items()}

            def _dms(v):
                try:
                    d, m, s = (float(x) for x in v)
                    return d + m / 60 + s / 3600
                except Exception:
                    return None

            lat, lon = _dms(gpsmap.get("GPSLatitude")), _dms(gpsmap.get("GPSLongitude"))
            if lat is not None and gpsmap.get("GPSLatitudeRef") == "S":
                lat = -lat
            if lon is not None and gpsmap.get("GPSLongitudeRef") == "W":
                lon = -lon
    except Exception as exc:  # pragma: no cover
        log.debug("EXIF 解析失败: %s", exc)
    return taken, lat, lon


def _make_thumb(img: Image.Image, dest: Path) -> None:
    s = get_settings()
    thumb = img.copy()
    thumb.thumbnail((s.thumb_max_edge, s.thumb_max_edge * 4), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if thumb.mode not in ("RGB", "RGBA"):
        thumb = thumb.convert("RGB")
    thumb.save(dest, "WEBP", quality=s.thumb_quality, method=4)


def _contract_no_for(
    db: Session,
    customer_id: int | None,
    contract_id: int | None = None,
    payment_id: int | None = None,
    cost_id: int | None = None,
    task_id: int | None = None,
) -> str:
    """照片目录名：优先取关联合同编号（如 ONE202610020001），取不到返回空串。

    顺序：直接挂合同 → 收款所属合同 → 成本所属合同 → 施工节点所属客户的合同 → 客户最早一份合同。
    """
    from ..models import Contract, ContractCost, Payment, Task

    def _no_of(cid: int | None) -> str:
        if not cid:
            return ""
        c = db.get(Contract, cid)
        return (c.no or "") if c else ""

    if contract_id:
        no = _no_of(contract_id)
        if no:
            return no
    if payment_id:
        p = db.get(Payment, payment_id)
        if p:
            no = _no_of(p.contract_id)
            if no:
                return no
    if cost_id:
        cc = db.get(ContractCost, cost_id)
        if cc:
            no = _no_of(cc.contract_id)
            if no:
                return no
    cid = customer_id
    if task_id:
        t = db.get(Task, task_id)
        if t:
            cid = t.customer_id
    if cid:
        c = db.scalars(
            select(Contract).where(Contract.customer_id == cid).order_by(Contract.id).limit(1)
        ).first()
        if c and c.no:
            return c.no
    return ""


def _storage_key(db: Session, customer_id: int, contract_id=None, payment_id=None, cost_id=None, task_id=None) -> str:
    """媒体目录名：合同编号优先，取不到合同则回落客户 ID；并做文件名安全过滤。"""
    no = _contract_no_for(
        db, customer_id, contract_id=contract_id, payment_id=payment_id, cost_id=cost_id, task_id=task_id
    )
    safe = "".join(ch for ch in no if ch.isalnum() or ch in "-_")
    return safe or str(customer_id)


def find_by_hash(db: Session, sha: str) -> Photo | None:
    return db.scalars(select(Photo).where(Photo.sha256 == sha).limit(1)).first()


def photos_by_cost(db: Session, cost_ids: list[int]) -> dict[int, list[Photo]]:
    """按成本记录分组凭证图片。"""
    ids = [i for i in cost_ids if i]
    if not ids:
        return {}
    rows = db.scalars(
        select(Photo).where(Photo.cost_id.in_(ids)).order_by(Photo.created_at.desc(), Photo.id.desc())
    ).all()
    grouped: dict[int, list[Photo]] = {}
    for ph in rows:
        grouped.setdefault(ph.cost_id, []).append(ph)
    return grouped


def photos_by_payment(db: Session, payment_ids: list[int]) -> dict[int, list[Photo]]:
    """按收款记录分组截图（收款流水/合同详情展示用）。"""
    ids = [i for i in payment_ids if i]
    if not ids:
        return {}
    rows = db.scalars(
        select(Photo).where(Photo.payment_id.in_(ids)).order_by(Photo.created_at.desc(), Photo.id.desc())
    ).all()
    grouped: dict[int, list[Photo]] = {}
    for ph in rows:
        grouped.setdefault(ph.payment_id, []).append(ph)
    return grouped


def photos_by_task(db: Session, task_ids: list[int]) -> dict[int, list[Photo]]:
    """按工序任务分组照片（施工看板/客户工序页展示用）。"""
    ids = [i for i in task_ids if i]
    if not ids:
        return {}
    rows = db.scalars(
        select(Photo).where(Photo.task_id.in_(ids)).order_by(Photo.created_at.desc(), Photo.id.desc())
    ).all()
    grouped: dict[int, list[Photo]] = {}
    for ph in rows:
        grouped.setdefault(ph.task_id, []).append(ph)
    return grouped


def save_photo(
    db: Session,
    customer_id: int,
    kind: str,
    raw: bytes,
    orig_name: str,
    uploaded_by: int | None,
    task_id: int | None = None,
    contract_id: int | None = None,
    payment_id: int | None = None,
    cost_id: int | None = None,
) -> tuple[Photo | None, str]:
    """保存一张照片。返回 (Photo, 状态信息)。

    路径规范：data/media/{合同编号|客户ID}/{kind}/{uuid}.ext
    目录名优先用合同编号（取不到合同则回落客户 ID）；禁止使用客户名或房号作为文件名/目录名。
    """
    if not raw:
        return None, "空文件"
    sha = _sha256_of(raw)
    kind = (kind or "other").strip() or "other"
    root = media_root()
    existing = find_by_hash(db, sha)

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as exc:
        return None, f"无法解析图片：{exc}"

    exif_taken, _lat, _lon = _read_exif(img)
    width, height = img.size

    if existing and (media_root() / existing.path).exists():
        # 去重：物理文件只存一份，新记录复用既有文件
        photo = Photo(
            customer_id=customer_id,
            task_id=task_id,
            contract_id=contract_id,
            payment_id=payment_id,
            cost_id=cost_id,
            kind=kind,
            path=existing.path,
            thumb_path=existing.thumb_path,
            width=existing.width,
            height=existing.height,
            taken_at=exif_taken or now_iso(),
            uploaded_by=uploaded_by,
            created_at=now_iso(),
            sha256=sha,
            orig_name=orig_name,
            size_bytes=len(raw),
        )
        db.add(photo)
        return photo, "重复文件，已复用既有物理文件"

    ext = (Path(orig_name).suffix or ".jpg").lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp", ".gif"}:
        ext = ".jpg"
    rel_dir = Path(
        _storage_key(db, customer_id, contract_id=contract_id, payment_id=payment_id, cost_id=cost_id, task_id=task_id)
    ) / kind
    fname = f"{uuid.uuid4().hex}{ext}"
    rel_path = rel_dir / fname
    dest = root / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)

    thumb_rel = rel_dir / f"{fname.rsplit('.', 1)[0]}_thumb.webp"
    try:
        _make_thumb(img, root / thumb_rel)
    except Exception as exc:  # pragma: no cover
        log.warning("缩略图生成失败 %s: %s", rel_path, exc)
        thumb_rel = None

    photo = Photo(
        customer_id=customer_id,
        task_id=task_id,
        contract_id=contract_id,
        payment_id=payment_id,
        cost_id=cost_id,
        kind=kind,
        path=str(rel_path),
        thumb_path=str(thumb_rel) if thumb_rel else None,
        width=width,
        height=height,
        taken_at=exif_taken or now_iso(),
        uploaded_by=uploaded_by,
        created_at=now_iso(),
        sha256=sha,
        orig_name=orig_name,
        size_bytes=len(raw),
    )
    db.add(photo)
    return photo, "已保存"


def delete_photos_for(db: Session, **filters) -> int:
    """删除与某个业务对象关联的全部照片记录（含物理文件，若无其他记录引用）。

    用于删除成本/收款/客户等业务对象之前先清理附件，避免外键约束阻止删除。
    """
    conds = []
    for field in ("customer_id", "contract_id", "payment_id", "cost_id", "task_id"):
        if field in filters and filters[field] is not None:
            conds.append(getattr(Photo, field) == filters[field])
    if not conds:
        return 0
    rows = list(db.scalars(select(Photo).where(*conds)).all())
    for ph in rows:
        delete_photo(db, ph)
    return len(rows)


def delete_photo(db: Session, photo: Photo) -> str:
    """删除记录；若物理文件已无其他记录引用则一并删除。"""
    others = db.scalars(
        select(Photo).where(Photo.path == photo.path).where(Photo.id != photo.id)
    ).all()
    db.delete(photo)
    if others:
        return "记录已删除（物理文件被其他记录共享，保留）"
    root = media_root()
    for rel in (photo.path, photo.thumb_path):
        if not rel:
            continue
        try:
            (root / rel).unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover
            log.warning("删除文件失败 %s: %s", rel, exc)
    return "记录与物理文件已删除"


def photo_file(rel: str) -> Path:
    root = media_root().resolve()
    target = (root / rel).resolve()
    if not str(target).startswith(str(root)):
        raise ValueError("非法路径")
    return target


def total_usage() -> dict:
    root = media_root()
    count, size = 0, 0
    for p in root.rglob("*"):
        if p.is_file():
            count += 1
            size += p.stat().st_size
    return {"files": count, "bytes": size}
