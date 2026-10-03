"""一次性：按当前 thumb_max_edge 重新生成全部照片缩略图。

用于缩略图尺寸调整后，把既有照片的缩略图按新尺寸重建。
用法（容器内）：python -m scripts.rethumb
"""
from __future__ import annotations

from PIL import Image
from sqlalchemy import select

from app.db import session_scope
from app.models import Photo
from app.services import photos as photo_svc


def main() -> None:
    done = failed = skipped = 0
    with session_scope() as db:
        photos = list(db.scalars(select(Photo)).all())
        for p in photos:
            if not p.thumb_path:
                skipped += 1
                continue
            full = photo_svc.photo_file(p.path)
            if not full.exists():
                skipped += 1
                continue
            try:
                img = Image.open(full)
                img.load()
                photo_svc._make_thumb(img, photo_svc.photo_file(p.thumb_path))
                done += 1
            except Exception as exc:  # pragma: no cover
                failed += 1
                print(f"[rethumb] 失败 #{p.id} {p.path}: {exc}")
    print(f"[rethumb] 完成：成功 {done}，跳过 {skipped}，失败 {failed}")


if __name__ == "__main__":
    main()
