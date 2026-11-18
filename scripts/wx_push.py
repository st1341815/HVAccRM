#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HVAccRM -> 微信小程序云开发 案例同步工具

用法（在 hvaccrm 容器内执行）：
    python /data/wx_cache/wx_push.py --sync     # 增量同步（cron 每 5 分钟调用）
    python /data/wx_cache/wx_push.py --all      # 强制全量重推
    python /data/wx_cache/wx_push.py --only <share_token>
    python /data/wx_cache/wx_push.py --probe    # 连通性自检
    python /data/wx_cache/wx_push.py --dry-run  # 离线演练，不联网不写库

配置来源（优先级从高到低）：
    1) 环境变量 WX_APPID / WX_SECRET / WX_ENV
    2) 文件 $DATA_DIR/wx_cache/wx.env（key=value，权限 600）

总开关：数据库 app_settings 表 wx.enabled = "1" 时才推送，由后台「微信小程序同步」页控制。
增量依据：本地 sync_state.json 里的内容指纹（字段 + 图片 size/mtime）。
删除清理：本地已删除的案例，会连带删除云端文档与云存储图片。

红线：客户姓名/电话/金额/完整房号永不出本机（映射表里没有这些字段）。
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.environ.get("DB_PATH", os.path.join(DATA_DIR, "crm.db"))
MEDIA_ROOT = os.path.join(DATA_DIR, "media")
CASES_MEDIA = os.path.join(MEDIA_ROOT, "cases")   # case_images.path 的实际根目录
CACHE_DIR = os.path.join(DATA_DIR, "wx_cache")
TOKEN_CACHE = os.path.join(CACHE_DIR, "token.json")
STATE_FILE = os.path.join(CACHE_DIR, "sync_state.json")
ENV_FILE = os.environ.get("WX_ENV_FILE", os.path.join(CACHE_DIR, "wx.env"))

API = "https://api.weixin.qq.com"
LONG_EDGE = 1440
JPEG_Q = 82
COVER_SIZE = (800, 640)
MAX_BATCH = 20          # 单次同步最多处理多少条，避免一次跑太久
TIME_BUDGET = 240       # 单次同步时间预算（秒），超出则留到下一次


def load_env_file(path):
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
    except Exception as exc:
        print("读取 %s 失败: %s" % (path, exc), file=sys.stderr)


load_env_file(ENV_FILE)

APPID = os.environ.get("WX_APPID", "")
SECRET = os.environ.get("WX_SECRET", "")
ENV = os.environ.get("WX_ENV", "")


# ---------------- 基础 ----------------
def log(msg):
    print(msg, flush=True)


def ensure_dir(path):
    if path and not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)


def http_json(url, payload, timeout=20):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_multipart(url, fields, file_field, filename, content, timeout=60):
    boundary = "----WxPush" + uuid.uuid4().hex
    body = bytearray()
    for key, value in fields.items():
        body += (
            '--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
            % (boundary, key, value)
        ).encode("utf-8")
    body += (
        '--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n" % (boundary, file_field, filename)
    ).encode("utf-8")
    body += content
    body += ("\r\n--%s--\r\n" % boundary).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=bytes(body),
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {"raw": raw[:200].decode("utf-8", "ignore")}


# ---------------- 开关 / 同步状态 ----------------
def read_switch():
    """读取后台开关 app_settings.wx.enabled。读不到按关闭处理（安全默认）。"""
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True, timeout=10)
        row = conn.execute("select value from app_settings where key='wx.enabled'").fetchone()
        conn.close()
        return bool(row and str(row[0]).strip() == "1")
    except Exception as exc:
        log("读取开关失败（按关闭处理）：%s" % exc)
        return False


def record_result(summary):
    """把同步结果回写到 app_settings，供后台页面展示。写失败不影响同步本身。"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        for key, value in (("wx.last_sync_at", now), ("wx.last_sync_result", summary)):
            conn.execute(
                "insert into app_settings(key, value, updated_at) values(?,?,?) "
                "on conflict(key) do update set value=excluded.value, updated_at=excluded.updated_at",
                (key, value, now),
            )
        conn.commit()
        conn.close()
    except Exception as exc:
        log("回写同步结果失败：%s" % exc)


def load_state():
    if not os.path.isfile(STATE_FILE):
        return {"tokens": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "tokens" not in data:
            return {"tokens": {}}
        return data
    except Exception:
        return {"tokens": {}}


def save_state(state):
    ensure_dir(CACHE_DIR)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_FILE)


# ---------------- token ----------------
def get_token(force=False):
    if not APPID or not SECRET:
        raise SystemExit("缺少 WX_APPID / WX_SECRET（env 文件: %s）" % ENV_FILE)

    if not force and os.path.isfile(TOKEN_CACHE):
        try:
            with open(TOKEN_CACHE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("appid") == APPID and cached.get("exp_at", 0) - 300 > time.time():
                return cached["access_token"]
        except Exception:
            pass

    payload = {"grant_type": "client_credential", "appid": APPID, "secret": SECRET}
    res = http_json(API + "/cgi-bin/stable_token", payload)
    token = res.get("access_token")
    if not token:
        raise SystemExit("取 token 失败: errcode=%s errmsg=%s" % (res.get("errcode"), res.get("errmsg")))

    ensure_dir(CACHE_DIR)
    with open(TOKEN_CACHE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "appid": APPID,
                "access_token": token,
                "expires_in": res.get("expires_in"),
                "exp_at": time.time() + int(res.get("expires_in") or 7200),
            },
            f,
        )
    return token


# ---------------- 数据库 ----------------
def db_conn():
    if not os.path.isfile(DB_PATH):
        raise SystemExit("找不到数据库: %s" % DB_PATH)
    conn = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def load_cases(conn, only=None):
    sql = "select * from cases"
    args = ()
    if only:
        sql += " where share_token = ?"
        args = (only,)
    sql += " order by id"
    cases = [dict(r) for r in conn.execute(sql, args)]

    for case in cases:
        rows = conn.execute(
            "select path, sort_order from case_images where case_id = ? order by sort_order, id",
            (case["id"],),
        )
        case["_images"] = [dict(r) for r in rows]
    return cases


def resolve_image(rel_path):
    if not rel_path:
        return None
    for base in (CASES_MEDIA, MEDIA_ROOT):
        candidate = os.path.join(base, rel_path)
        if os.path.isfile(candidate):
            return candidate
    return None


def fingerprint(case):
    """内容指纹：公开字段 + 每张图的 path/size/mtime。任何变更都会变。"""
    parts = [
        case.get("project_name") or "",
        case.get("building") or "",
        case.get("stage") or "",
        case.get("work_date") or "",
        case.get("product_type") or "",
        case.get("installer_name") or "",
        case.get("share_expires_at") or "",
        str(case.get("share_disabled") or 0),
    ]
    for img in case.get("_images") or []:
        rel = img.get("path") or ""
        real = resolve_image(rel)
        if real:
            st = os.stat(real)
            parts.append("%s|%d|%d" % (rel, st.st_size, int(st.st_mtime)))
        else:
            parts.append("%s|missing" % rel)
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()


# ---------------- 图片转码 ----------------
def transcode(src_path, dst_path, long_edge=LONG_EDGE, quality=JPEG_Q):
    from PIL import Image

    ensure_dir(os.path.dirname(dst_path))
    with Image.open(src_path) as im:
        im = im.convert("RGB")  # convert 丢弃 EXIF/ICC，等同 strip
        w, h = im.size
        scale = float(long_edge) / max(w, h)
        if scale < 1:
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        im.save(dst_path, "JPEG", quality=quality, optimize=True, progressive=True)
        return im.size[0], im.size[1], os.path.getsize(dst_path)


def make_cover(src_path, dst_path):
    from PIL import Image

    ensure_dir(os.path.dirname(dst_path))
    target_w, target_h = COVER_SIZE
    with Image.open(src_path) as im:
        im = im.convert("RGB")
        w, h = im.size
        target_ratio = float(target_w) / target_h
        if float(w) / h > target_ratio:
            new_w = int(h * target_ratio)
            left = (w - new_w) // 2
            im = im.crop((left, 0, left + new_w, h))
        else:
            new_h = int(w / target_ratio)
            top = (h - new_h) // 2
            im = im.crop((0, top, w, top + new_h))
        im = im.resize((target_w, target_h), Image.LANCZOS)
        im.save(dst_path, "JPEG", quality=85, optimize=True, progressive=True)
        return os.path.getsize(dst_path)


# ---------------- 云端操作 ----------------
def upload_file(token, local_path, cloud_path):
    res = http_json(
        API + "/tcb/uploadfile?access_token=" + token,
        {"env": ENV, "path": cloud_path},
    )
    if res.get("errcode") not in (0, None):
        raise RuntimeError("uploadfile 失败: %s %s" % (res.get("errcode"), res.get("errmsg")))

    with open(local_path, "rb") as f:
        content = f.read()

    fields = {
        "key": cloud_path,
        "Signature": res["authorization"],
        "x-cos-security-token": res["token"],
        "x-cos-meta-fileid": res["cos_file_id"],
    }
    http_multipart(res["url"], fields, "file", os.path.basename(local_path), content)
    return res["file_id"]


def db_query(token, query):
    res = http_json(API + "/tcb/databasequery?access_token=" + token, {"env": ENV, "query": query})
    if res.get("errcode") not in (0, None):
        raise RuntimeError("databasequery 失败: %s %s" % (res.get("errcode"), res.get("errmsg")))
    return res


def db_update(token, query):
    res = http_json(API + "/tcb/databaseupdate?access_token=" + token, {"env": ENV, "query": query})
    if res.get("errcode") not in (0, None):
        raise RuntimeError("databaseupdate 失败: %s %s" % (res.get("errcode"), res.get("errmsg")))
    return res


def db_delete_doc(token, doc_id):
    return db_update(token, 'db.collection("cases").doc("%s").remove()' % doc_id)


def batch_delete_files(token, file_ids):
    ids = [f for f in (file_ids or []) if f]
    if not ids:
        return None
    return http_json(
        API + "/tcb/batchdeletefile?access_token=" + token,
        {"env": ENV, "fileid_list": ids},
    )


def upsert_doc(token, doc):
    """doc().set() 本身就是 upsert：不存在时插入（updated 返回 0），存在时覆盖。"""
    doc_id = doc["_id"]
    payload = {k: v for k, v in doc.items() if k != "_id"}
    data_js = json.dumps(payload, ensure_ascii=False)
    res = db_update(token, 'db.collection("cases").doc("%s").set({data:%s})' % (doc_id, data_js))
    return "upsert ok (updated=%s)" % (res.get("updated") or 0)


# ---------------- 映射 ----------------
def build_doc(case, image_meta, sync_ver=1):
    token = case.get("share_token") or ""
    site = " ".join(p for p in (case.get("project_name"), case.get("building")) if p)
    images = [{"f": m["file_id"], "w": m["w"], "h": m["h"]} for m in image_meta]
    created = case.get("created_at") or ""
    return {
        "_id": token,
        "site": site,
        "project": case.get("project_name") or "",
        "stage": case.get("stage") or "",
        "work_date": case.get("work_date") or "",
        "product_type": case.get("product_type") or "",
        "installer": case.get("installer_name") or "",
        "cover": image_meta[0]["cover_id"] if image_meta else "",
        "images": images,
        "expires_at": case.get("share_expires_at") or "",
        "disabled": bool(case.get("share_disabled")),
        "sync_ver": sync_ver,
        "published_at": created.replace(" ", "T") + "+08:00" if created else "",
    }


def push_case(token, case, sync_ver=1):
    """上传图片 + 写文档，返回 (doc, file_ids)。"""
    share_token = case.get("share_token") or ""
    out_dir = os.path.join(CACHE_DIR, share_token)
    imgs = case.get("_images") or []

    meta = []
    file_ids = []
    for idx, img in enumerate(imgs):
        src = resolve_image(img.get("path"))
        if not src:
            log("  缺图 case=%s path=%s" % (case["id"], img.get("path")))
            continue

        local = os.path.join(out_dir, "%d.jpg" % idx)
        w, h, size = transcode(src, local)
        cloud_path = "cases/%s/%d.jpg" % (share_token, idx)
        file_id = upload_file(token, local, cloud_path)
        file_ids.append(file_id)

        cover_id = ""
        if idx == 0:
            cover_local = os.path.join(out_dir, "cover.jpg")
            make_cover(src, cover_local)
            cover_id = upload_file(token, cover_local, "cases/%s/cover.jpg" % share_token)
            file_ids.append(cover_id)

        meta.append({"file_id": file_id, "cover_id": cover_id, "w": w, "h": h, "size": size})
        log("  [%d/%d] %s -> %.0f KB" % (idx + 1, len(imgs), cloud_path, size / 1024.0))

    if not meta:
        return None, []

    doc = build_doc(case, meta, sync_ver)
    upsert_doc(token, doc)
    return doc, file_ids


# ---------------- 各模式 ----------------
def probe():
    log("env file      : %s (%s)" % (ENV_FILE, "exists" if os.path.isfile(ENV_FILE) else "MISSING"))
    log("appid         : %s" % (APPID or "(空)"))
    log("secret        : %s" % ("***" if SECRET else "(空)"))
    log("env id        : %s" % (ENV or "(空)"))
    log("开关 wx.enabled: %s" % ("开启" if read_switch() else "关闭"))
    if not ENV:
        log("缺 WX_ENV，后续无法上传/写库")
        return

    token = get_token(force=True)
    log("token         : OK")
    for coll in ("cases", "members"):
        try:
            res = db_query(token, 'db.collection("%s").count()' % coll)
            log("collection %-7s -> %s" % (coll, json.dumps(res, ensure_ascii=False)[:200]))
        except Exception as exc:
            log("collection %-7s -> ERROR %s" % (coll, exc))


def dry_run(cases):
    log("== DRY RUN（不联网、不写入）==")
    total = 0
    missing = 0
    for case in cases:
        token = case.get("share_token")
        out_dir = os.path.join(CACHE_DIR, token or str(case["id"]))
        meta = []
        for idx, img in enumerate(case.get("_images") or []):
            src = resolve_image(img.get("path"))
            if not src:
                missing += 1
                continue
            dst = os.path.join(out_dir, "%d.jpg" % idx)
            w, h, size = transcode(src, dst)

            cover_id = ""
            if not meta:
                make_cover(src, os.path.join(out_dir, "cover.jpg"))
                cover_id = "cloud://DRYRUN/%s/cover.jpg" % token

            meta.append(
                {
                    "file_id": "cloud://DRYRUN/%s/%d.jpg" % (token, idx),
                    "cover_id": cover_id,
                    "w": w,
                    "h": h,
                    "size": size,
                }
            )
            total += 1

        doc = build_doc(case, meta)
        log("-" * 60)
        log("case id=%s token=%s images=%d fp=%s" % (case["id"], token, len(meta), fingerprint(case)[:12]))
        log(json.dumps(doc, ensure_ascii=False, indent=2))

    log("=" * 60)
    log("案例 %d 条，可转码图片 %d 张，缺失源文件 %d 张" % (len(cases), total, missing))


def sync(cases):
    """增量同步：只推指纹变化的案例，清理本地已删除的。"""
    if not read_switch():
        log("同步开关关闭（后台 → 微信小程序同步 → 启用定时同步），本次跳过")
        return

    state = load_state()
    known = state.get("tokens") or {}
    local_tokens = {}

    token = get_token()
    started = time.time()

    added = changed = deleted = failed = skipped = 0

    for case in cases:
        share_token = case.get("share_token")
        if not share_token:
            continue
        local_tokens[share_token] = True

        fp = fingerprint(case)
        prev = known.get(share_token)
        if prev and prev.get("fp") == fp:
            skipped += 1
            continue

        is_new = prev is None
        try:
            doc, file_ids = push_case(token, case, sync_ver=(prev or {}).get("ver", 0) + 1)
            if not doc:
                log("case id=%s 无可上传图片，跳过" % case["id"])
                continue
            known[share_token] = {
                "fp": fp,
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "ver": doc["sync_ver"],
                "files": file_ids,
            }
            save_state({"tokens": known})
            if is_new:
                added += 1
                log("case id=%s 新增同步完成" % case["id"])
            else:
                changed += 1
                log("case id=%s 更新同步完成" % case["id"])
        except Exception as exc:
            failed += 1
            log("case id=%s 同步失败：%s" % (case["id"], exc))

        if time.time() - started > TIME_BUDGET:
            log("已达单次时间预算，剩余变更留到下次")
            break

    # 清理：状态里有、但本地已经不存在的案例
    if len(local_tokens) < len(known):
        for share_token in list(known.keys()):
            if share_token in local_tokens:
                continue
            try:
                db_delete_doc(token, share_token)
                batch_delete_files(token, known[share_token].get("files"))
                known.pop(share_token, None)
                save_state({"tokens": known})
                deleted += 1
                log("远端已删除案例 token=%s" % share_token)
            except Exception as exc:
                failed += 1
                log("删除远端案例 %s 失败：%s" % (share_token, exc))

    elapsed = int(time.time() - started)
    summary = "新增 %d / 更新 %d / 删除 %d / 未变 %d / 失败 %d，耗时 %ds" % (
        added, changed, deleted, skipped, failed, elapsed,
    )
    log("同步完成：" + summary)
    record_result(summary)


def full_push(cases):
    token = get_token()
    log("token 就绪，环境 = %s" % ENV)
    if not ENV:
        raise SystemExit("缺少 WX_ENV")

    state = load_state()
    known = state.get("tokens") or {}

    for case in cases:
        share_token = case.get("share_token")
        if not share_token:
            log("跳过 case id=%s：无 share_token" % case["id"])
            continue

        doc, file_ids = push_case(token, case, sync_ver=(known.get(share_token) or {}).get("ver", 0) + 1)
        if not doc:
            log("跳过 case id=%s：无可上传图片" % case["id"])
            continue

        known[share_token] = {
            "fp": fingerprint(case),
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ver": doc["sync_ver"],
            "files": file_ids,
        }
        save_state({"tokens": known})
        log("case id=%s -> _id=%s cover=%s" % (case["id"], doc["_id"], doc["cover"]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sync", action="store_true", help="增量同步（定时任务用）")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--only", default="")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    if args.probe:
        probe()
        return

    conn = db_conn()
    cases = load_cases(conn, args.only or None)
    if not cases:
        raise SystemExit("没有匹配的案例")

    if args.sync:
        sync(cases)
    elif args.dry_run:
        dry_run(cases)
    elif args.all or args.only:
        full_push(cases)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
