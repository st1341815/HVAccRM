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


def find_by_hash(db: Session, sha: str) -> Photo | None:
    return db.scalars(select(Photo).where(Photo.sha256 == sha).limit(1)).first()


def save_photo(
    db: Session,
    customer_id: int,
    kind: str,
    raw: bytes,
    orig_name: str,
    uploaded_by: int | None,
    task_id: int | None = None,
    contract_id: int | None = None,
) -> tuple[Photo | None, str]:
    """保存一张照片。返回 (Photo, 状态信息)。

    路径规范：data/media/{customer_id}/{kind}/{uuid}.jpg|png|webp …
    禁止使用客户名或房号作为文件名/目录名。
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
    rel_dir = Path(str(customer_id)) / kind
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
