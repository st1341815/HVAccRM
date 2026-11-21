#!/usr/bin/env python3
"""案例快照导出（只读，给微信小程序用）。

设计原则
--------
1. **绝不碰线上库**：先用 sqlite3 的 backup API 把整库复制到临时文件，再读副本。
   全程不获取写锁、不进 CRM 的连接池，导出期间 CRM 照常读写。
2. **不依赖 app 包**：只用标准库 + Pillow（可选），本机和容器内都能直接跑。
3. **只导出可公开字段**：不含 customer_id / installer_id / installer_name /
   合同金额 / 联系方式。新增字段前先想清楚会不会泄露隐私。
4. **单向快照**：产物是静态文件，小程序只读；CRM 不依赖本脚本，脚本挂了不影响业务。

用法
----
    # 只生成 cases.json（不动图片）
    python3 scripts/export_case_snapshot.py \\
        --db /vol1/1000/Docker/Hvaccrm/data/crm.db \\
        --media /vol1/1000/Docker/Hvaccrm/data/media \\
        --out dist/miniapp

    # 同时把图片压缩后复制出来，准备上传 CDN
    python3 scripts/export_case_snapshot.py ... --copy-images --max-edge 1600

    # 看会导出什么，但不写盘
    python3 scripts/export_case_snapshot.py ... --dry-run

产物
----
    <out>/cases.json               小程序一次拉全量（约 20 KB / 上百案例）
    <out>/images/cases/<id>/<f>    仅 --copy-images 时生成，按 CDN 目录结构摆放
    <out>/index.html               本地预览页（可选，--preview）

CDN 约定
--------
    --base https://img.example.com/    →  JSON 里的图片 URL 为
                                    https://img.example.com/cases/<id>/<f>
    images/ 目录结构与之一一对应，直接 rsync/上传即可。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 1


def _has_pillow() -> bool:
    """探测 Pillow 是否真的可用（装了但缺 _imaging C 扩展的情况真实存在）。"""
    try:
        from PIL import Image  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


HAS_PIL = _has_pillow()


# ---------------------------------------------------------------- 工具
def log(msg: str) -> None:
    print(msg, flush=True)


def snapshot_db(src: Path, dst: Path) -> None:
    """用 backup API 做一致性副本：不阻塞写、不持有长事务。"""
    if not src.is_file():
        raise SystemExit(f"数据库不存在：{src}")
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)  # 单次调用，原子快照
    finally:
        d.close()
        s.close()


def is_expired(row: sqlite3.Row, now: datetime) -> bool:
    """share_expires_at 为空 = 不限；早于当前时间 = 已过期。"""
    raw = (row["share_expires_at"] or "").strip()
    if not raw:
        return False
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[: len(fmt) + 2].strip(), fmt) < now
        except ValueError:
            continue
    return False  # 解析不了就当不过期，宁可多展示也不要漏


def join_base(base: str, rel: str) -> str:
    return f"{base.rstrip('/')}/{rel.lstrip('/')}"


# ---------------------------------------------------------------- 主流程
def build(
    db_path: Path,
    media_dir: Path,
    out_dir: Path,
    base: str,
    copy_images: bool,
    max_edge: int,
    include_disabled: bool,
    dry_run: bool,
) -> dict:
    now = datetime.now()

    with tempfile.TemporaryDirectory(prefix="casesnap-") as tmp:
        tmp_db = Path(tmp) / "crm.db"
        snapshot_db(db_path, tmp_db)
        conn = sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

        rows = conn.execute(
            """
            SELECT id, share_token, project_name, building, room_label,
                   stage, work_date, product_type,
                   share_disabled, share_expires_at
              FROM cases
             ORDER BY COALESCE(NULLIF(work_date, ''), '0000-00-00') DESC, id DESC
            """
        ).fetchall()

        img_rows = conn.execute(
            """
            SELECT id, case_id, path, thumb_path, sort_order
              FROM case_images
             ORDER BY case_id, sort_order, id
            """
        ).fetchall()
        conn.close()

    imgs_by_case: dict[int, list[sqlite3.Row]] = {}
    for r in img_rows:
        imgs_by_case.setdefault(r["case_id"], []).append(r)

    cases: list[dict] = []
    skipped: dict[str, int] = {"已失效": 0, "已过期": 0, "无图片": 0}

    for r in rows:
        if not include_disabled and (r["share_disabled"] or 0) == 1:
            skipped["已失效"] += 1
            continue
        if is_expired(r, now):
            skipped["已过期"] += 1
            continue

        imgs = imgs_by_case.get(r["id"], [])
        if not imgs:
            skipped["无图片"] += 1
            continue

        site = " ".join(p for p in (r["project_name"], r["building"]) if p).strip()
        if not site:  # 兜底：老数据可能没拆字段
            site = (r["room_label"] or "").strip()

        title = " · ".join(
            p for p in (
                f"{r['product_type']}施工案例纪实" if r["product_type"] else "施工案例纪实",
                site,
            ) if p
        )

        items = []
        for img in imgs:
            if not img["path"]:
                continue
            # 产物扩展名必须与 _copy_images() 的实际产物严格一致，否则小程序 404。
            # 有 Pillow → 统一转 WebP；没有 → 各自保留原后缀（原图 .jpg / 缩略图 .webp）。
            stem = Path(img["path"]).stem
            has_thumb = bool(img["thumb_path"])
            if HAS_PIL:
                url_ext = thumb_ext = ".webp"
            else:
                url_ext = Path(img["path"]).suffix or ".jpg"
                thumb_ext = (
                    (Path(img["thumb_path"]).suffix or ".jpg") if has_thumb else url_ext
                )
            items.append({
                "id": img["id"],
                "url": join_base(base, f"cases/{r['id']}/{stem}{url_ext}"),
                # 无缩略图时回落到原图，避免小程序拿到 404 的 thumb
                "thumb": join_base(
                    base,
                    f"cases/{r['id']}/{stem}_thumb{thumb_ext}" if has_thumb
                    else f"cases/{r['id']}/{stem}{url_ext}",
                ),
                "_src": {"image": img["path"], "thumb": img["thumb_path"]},
            })

        if not items:
            skipped["无图片"] += 1
            continue

        cases.append({
            "id": r["id"],
            "slug": r["share_token"],
            "title": title,
            "site": site,
            "work_date": r["work_date"] or "",
            "stage": r["stage"] or "",
            "product_type": r["product_type"] or "",
            "cover": items[0]["thumb"],
            "image_count": len(items),
            "images": items,
        })

    # 筛选维度从实际数据里长出来，不写死
    def uniq(key: str) -> list[str]:
        seen: list[str] = []
        for c in cases:
            v = c.get(key) or ""
            if v and v not in seen:
                seen.append(v)
        return seen

    payload = {
        "schema": SCHEMA_VERSION,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "base": base.rstrip("/") + "/",
        "total": len(cases),
        "filters": {
            "product_types": uniq("product_type"),
            "stages": uniq("stage"),
        },
        "cases": cases,
    }

    if dry_run:
        log(f"[dry-run] 将导出 {len(cases)} 个案例、"
            f"{sum(c['image_count'] for c in cases)} 张图 → {out_dir}")
        log(f"[dry-run] 过滤统计：{skipped}")
        return payload

    out_dir.mkdir(parents=True, exist_ok=True)

    if copy_images:
        _copy_images(cases, media_dir, out_dir, max_edge)

    # 内部字段不外泄：_src 只用于复制阶段
    for c in cases:
        for item in c["images"]:
            item.pop("_src", None)

    out_json = out_dir / "cases.json"
    out_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(f"✓ {out_json}  ({out_json.stat().st_size:,} 字节)")
    log(f"  案例 {len(cases)} 个 / 图片 {sum(c['image_count'] for c in cases)} 张")
    if any(skipped.values()):
        log(f"  已跳过：{ {k: v for k, v in skipped.items() if v} }")
    return payload


def _copy_images(cases: list[dict], media_dir: Path, out_dir: Path, max_edge: int) -> None:
    """按 CDN 目录结构摆好图；有 Pillow 时把长边压到 max_edge 并转 WebP。"""
    Image = None
    if HAS_PIL:
        from PIL import Image  # noqa: PLC0415
    else:
        log("  · 未检测到可用的 Pillow，按原格式复制（不压缩）")

    # DB 里 path 形如 "cases/<id>/<file>"，而存储根就是 media_dir/cases，
    # 所以物理路径是 media_dir/cases/cases/<id>/<file>（双层是现有实现，别改）。
    src_root = media_dir / "cases"
    dst_root = out_dir / "images" / "cases"
    n_ok = n_miss = 0
    saved_bytes = 0

    for c in cases:
        for item in c["images"]:
            for _kind, rel_val in item["_src"].items():
                if not rel_val:
                    continue
                src = src_root / rel_val
                if not src.is_file():
                    n_miss += 1
                    log(f"  ! 缺图 {rel_val}")
                    continue
                ext = ".webp" if HAS_PIL else (src.suffix or ".jpg")
                dst = dst_root / Path(rel_val).parent.name / (Path(rel_val).stem + ext)
                dst.parent.mkdir(parents=True, exist_ok=True)

                if not HAS_PIL:
                    shutil.copy2(src, dst)
                    n_ok += 1
                    saved_bytes += 0
                    continue

                try:
                    with Image.open(src) as im:
                        if max(im.size) > max_edge:
                            im.thumbnail((max_edge, max_edge * 4), Image.LANCZOS)
                        if im.mode not in ("RGB", "RGBA"):
                            im = im.convert("RGB")
                        before = src.stat().st_size
                        im.save(dst, "WEBP", quality=82, method=4)
                        saved_bytes += max(0, before - dst.stat().st_size)
                        n_ok += 1
                except Exception as exc:  # noqa: BLE001
                    log(f"  ! 压缩失败 {rel_val}: {exc}")

    log(f"  图片复制 {n_ok} 个，缺失 {n_miss} 个，节省 {saved_bytes / 1048576:.1f} MB")


# ---------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="导出案例快照（只读，供小程序使用）")
    p.add_argument("--db", default="/data/crm.db", help="CRM 的 sqlite 库路径")
    p.add_argument("--media", default="", help="媒体目录（默认 <db 同级>/media）")
    p.add_argument("--out", default="dist/miniapp", help="输出目录")
    p.add_argument("--base", default="https://img.example.com/", help="图片 CDN 前缀")
    p.add_argument("--copy-images", action="store_true", help="复制并压缩图片到 <out>/images")
    p.add_argument("--max-edge", type=int, default=1600, help="图片长边上限，默认 1600")
    p.add_argument("--include-disabled", action="store_true", help="包含已失效/已过期案例")
    p.add_argument("--dry-run", action="store_true", help="只统计不写盘")
    a = p.parse_args(argv)

    db_path = Path(a.db).expanduser().resolve()
    media_dir = Path(a.media).expanduser().resolve() if a.media else db_path.parent / "media"
    out_dir = Path(a.out).expanduser().resolve()

    if not media_dir.is_dir():
        log(f"! 媒体目录不存在：{media_dir}（--copy-images 会被跳过）")
        if a.copy_images:
            a.copy_images = False

    build(
        db_path=db_path,
        media_dir=media_dir,
        out_dir=out_dir,
        base=a.base,
        copy_images=a.copy_images,
        max_edge=a.max_edge,
        include_disabled=a.include_disabled,
        dry_run=a.dry_run,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
