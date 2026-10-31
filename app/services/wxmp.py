"""微信公众号草稿发布（HVAccRM 内置，零外部依赖）。

设计约束（用户 2026-10-31 明确要求）：
  **必须打包在 HVAccRM 项目里，迁移时不能依赖 Octop 之类的宿主环境。**
  所以 Chromium 直接装进容器，登录态存 /data 卷，全部逻辑走本进程。

整体流程：
  1. ensure_browser()  启动容器内 Chromium（headless + remote-debugging）
  2. start_login()     打开 mp.weixin.qq.com，截图二维码给用户扫
  3. token 存在         = 已登录（从后台 URL 里读）
  4. publish_case()    填标题 → 写正文 → 传全部图 → 存草稿

铁律（踩过坑，改动前先读完）：
  1. **绝不出现「发表」按钮的选择器**。只点「保存为草稿」。
     2026-10-31 首次测试时坐标点击落在「发表」范围内，差点误发。
  2. 正文注入只用 execCommand('insertHTML')；ClipboardEvent('paste') 会被
     ProseMirror 拦掉（实测 dispatchEvent 返回 false、插入 0 字）。
  3. 图片必须走 CDP DOM.setFileInputFiles。上传后要校验 src 变成 qpic.cn
     （=已转存素材库），否则发表时是裂图。
  4. 等图时判定「总数 + CDN 数同时达标」，只看 cdn>=1 会误报丢图。
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from ..config import get_settings
from . import cdp as cdp_mod

PORT = 9223
# 单浏览器 = 单会话，多用户并发必须串行
_LOCK = threading.Lock()

BRAND_FALLBACK = "HVAccRM"


# ------------------------------------------------------------------ 路径
def _data_dir() -> Path:
    return Path(get_settings().data_dir)


def profile_dir() -> Path:
    return _data_dir() / "wxmp-profile"


def work_dir() -> Path:
    d = _data_dir() / "wxmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def qr_path() -> Path:
    return work_dir() / "qr.png"


def pid_path() -> Path:
    return work_dir() / "chrome.pid"


# ------------------------------------------------------------------ 浏览器
def chromium_bin() -> str | None:
    for name in ("chromium", "chromium-browser", "google-chrome"):
        from shutil import which

        p = which(name)
        if p:
            return p
    return None


def is_available() -> bool:
    """容器里到底有没有装浏览器。没装时前端应该显示「未启用」而不是报错。"""
    return chromium_bin() is not None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def browser_pid() -> int | None:
    try:
        pid = int(pid_path().read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if _pid_alive(pid) else None


def browser_ready(timeout: float = 1.0) -> bool:
    """CDP 端口能否应答（比查进程更可靠，进程活着但端口没起来也算没ready）。"""
    try:
        cdp_mod.targets(PORT)
        return True
    except Exception:
        return False


def ensure_browser(wait: float = 25.0) -> None:
    """启动或复用 Chromium。已在跑就直接返回。"""
    if browser_ready():
        return
    bin_path = chromium_bin()
    if not bin_path:
        raise RuntimeError("容器内没有 Chromium，无法使用公众号同步功能")

    profile_dir().mkdir(parents=True, exist_ok=True)
    args = [
        bin_path,
        "--no-sandbox",              # 容器内以 root 跑，必须关沙箱
        "--disable-dev-shm-usage",   # /dev/shm 太小会导致崩溃
        "--disable-gpu",
        "--headless=new",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        f"--remote-debugging-port={PORT}",
        f"--user-data-dir={profile_dir()}",
        "about:blank",
    ]
    log = open(work_dir() / "chrome.log", "ab")
    proc = subprocess.Popen(args, stdout=log, stderr=log, start_new_session=True)
    pid_path().write_text(str(proc.pid))

    deadline = time.time() + wait
    while time.time() < deadline:
        if browser_ready():
            return
        time.sleep(0.5)
    raise RuntimeError("Chromium 启动超时（CDP 端口无响应）")


def stop_browser() -> None:
    pid = browser_pid()
    if pid:
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    try:
        pid_path().unlink()
    except OSError:
        pass


# ------------------------------------------------------------------ 会话
LOGIN_URL = "https://mp.weixin.qq.com/"
TOKEN_RE = re.compile(r"token=(\d+)")

JS_QR_RECT = """(()=>{
  const i=document.querySelector('.qrcode img, img.qrcode, img[src*="scanloginqrcode"]');
  if(!i) return null;
  const r=i.getBoundingClientRect();
  return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),
    w:Math.round(r.width),h:Math.round(r.height)});
})()"""


def _open(page_url: str = LOGIN_URL) -> cdp_mod.CDP:
    ws = cdp_mod.page_ws(PORT, match="mp.weixin.qq.com")
    c = cdp_mod.CDP(ws)
    cur = c.js("location.href") or ""
    if page_url not in cur:
        c.call("Page.navigate", url=page_url)
        time.sleep(2.0)
    return c


def login_state() -> dict:
    """未登录 → 返回二维码截图路径；已登录 → 返回 token。"""
    if not is_available():
        return {"ok": False, "reason": "no_browser"}
    ensure_browser()
    c = _open(LOGIN_URL)
    try:
        has_qr = c.js("""(()=>!!document.querySelector('img[src*="scanloginqrcode"]'))()""")
        if has_qr:
            shot = c.screenshot_png()
            _crop_qr(c, shot, qr_path())
            return {"ok": True, "logged_in": False, "qr": True}
        token = _read_token(c)
        if token:
            return {"ok": True, "logged_in": True, "token": token}
        return {"ok": True, "logged_in": False, "qr": False}
    finally:
        c.close()


def _read_token(c: cdp_mod.CDP) -> str:
    url = c.js("location.href") or ""
    m = TOKEN_RE.search(url)
    if m:
        return m.group(1)
    try:
        return c.js("""(()=>{
          const m=location.href.match(/token=(\\d+)/);
          return m?m[1]:'';
        })()""") or ""
    except Exception:
        return ""


def _crop_qr(c: cdp_mod.CDP, png: bytes, dest: Path) -> bool:
    """把二维码从整页截图里裁出来并放大——整页 1440x900 里它只有 122px，手机扫不清。"""
    raw = c.js(JS_QR_RECT)
    if not raw:
        return False
    try:
        from PIL import Image

        rect = json.loads(raw)
        im = Image.open(io.BytesIO(png)).convert("RGB")
        pad = 8
        box = (
            max(0, rect["x"] - pad),
            max(0, rect["y"] - pad),
            min(im.width, rect["x"] + rect["w"] + pad),
            min(im.height, rect["y"] + rect["h"] + pad),
        )
        crop = im.crop(box)
        crop = crop.resize((crop.width * 4, crop.height * 4), Image.NEAREST)
        dest.parent.mkdir(parents=True, exist_ok=True)
        crop.save(dest, "PNG")
        return True
    except Exception:
        # 裁剪失败就退回整页截图，宁可图大一点也不能没有二维码
        dest.write_bytes(png)
        return False


# ------------------------------------------------------------------ 发布
EDITOR_URL = (
    "https://mp.weixin.qq.com/cgi-bin/appmsg?t=media/appmsg_edit_v2&action=edit"
    "&isNew=1&type=77&createType=0&token={token}&lang=zh_CN"
)

JS_FOCUS_TITLE = """(()=>{
  const ed=document.querySelector('div.ProseMirror[data-placeholder="请在这里输入标题"]');
  if(!ed) return 'NO_TITLE';
  ed.focus();
  const r=document.createRange(); r.selectNodeContents(ed);
  const s=getSelection(); s.removeAllRanges(); s.addRange(r);
  return 'ok';
})()"""

JS_TITLE_TEXT = """(()=>{
  const e=document.querySelector('div.ProseMirror[data-placeholder="请在这里输入标题"]');
  return e?e.innerText:'';
})()"""

JS_FOCUS_BODY = """(()=>{
  const eds=[...document.querySelectorAll('div.ProseMirror')];
  if(!eds.length) return 'NO_BODY';
  const ed=eds[eds.length-1];
  ed.focus();
  const r=document.createRange(); r.selectNodeContents(ed); r.collapse(false);
  const s=getSelection(); s.removeAllRanges(); s.addRange(r);
  return 'ok';
})()"""

JS_PROBE = """(()=>{
  const eds=[...document.querySelectorAll('div.ProseMirror')];
  if(!eds.length) return JSON.stringify({imgs:0,cdn:0,text:'',ready:false});
  const ed=eds[eds.length-1];
  const imgs=[...ed.querySelectorAll('img')].filter(i=>!i.classList.contains('ProseMirror-separator'));
  return JSON.stringify({
    ready:true,
    imgs:imgs.length,
    cdn:imgs.filter(i=>(i.src||'').includes('qpic.cn')).length,
    text:ed.innerText.replace(/\\n+/g,'|').slice(0,150)
  });
})()"""


def _wait_editor(c: cdp_mod.CDP, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = json.loads(c.js(JS_PROBE) or "{}")
        if st.get("ready"):
            return
        time.sleep(0.5)
    raise RuntimeError("图文编辑页加载超时")


def _set_title(c: cdp_mod.CDP, text: str) -> None:
    if c.js(JS_FOCUS_TITLE) != "ok":
        raise RuntimeError("找不到标题输入框")
    c.js(f"document.execCommand('insertText', false, {json.dumps(text)})")
    got = (c.js(JS_TITLE_TEXT) or "").strip()
    if got != text.strip():
        raise RuntimeError(f"标题写入失败：期望 {text!r} 实得 {got!r}")


def _set_body(c: cdp_mod.CDP, html: str) -> None:
    if c.js(JS_FOCUS_BODY) != "ok":
        raise RuntimeError("找不到正文编辑器")
    if not c.js(f"document.execCommand('insertHTML', false, {json.dumps(html)})"):
        raise RuntimeError("正文写入失败（insertHTML 返回 false）")


def _insert_images(c: cdp_mod.CDP, files: list[str], per_image_timeout: float = 90) -> dict:
    for i, path in enumerate(files, start=1):
        c.js(JS_FOCUS_BODY)
        c.set_files("input[type=file]", [path])
        deadline = time.time() + per_image_timeout
        while time.time() < deadline:
            time.sleep(0.8)
            st = json.loads(c.js(JS_PROBE) or "{}")
            # 总数和 CDN 数都达标才算这张传完，否则会把「最后一张还在传」误判成丢图
            if st.get("imgs", 0) >= i and st.get("cdn", 0) >= i:
                break
    return json.loads(c.js(JS_PROBE) or "{}")


def _save_draft(c: cdp_mod.CDP) -> dict:
    """只点「保存为草稿」。三重校验：文本相等 + 可见 + 未禁用。"""
    raw = c.js("""(()=>{
      const btns=[...document.querySelectorAll('button')];
      const hits=btns.filter(b=>(b.innerText||'').trim()==='保存为草稿');
      const usable=hits.filter(b=>b.offsetParent!==null && !b.disabled);
      if(!usable.length) return JSON.stringify({ok:false,reason:'not_found',total:hits.length});
      const b=usable[0];
      b.scrollIntoView({block:'center'});
      b.click();
      return JSON.stringify({ok:true});
    })()""")
    return json.loads(raw or '{"ok":false}')


def _saved_ok(c: cdp_mod.CDP) -> bool:
    url = c.js("location.href") or ""
    return "action=edit" in url and "isNew=1" not in url


def publish_case(db, case, image_files: list[str], html: str, title: str) -> dict:
    """发布一篇草稿。调用方负责取数、生成 html、解析标题。"""
    with _LOCK:
        if not is_available():
            return {"ok": False, "error": "容器内没有 Chromium"}
        ensure_browser()
        c = _open(LOGIN_URL)
        try:
            if not _read_token(c):
                return {"ok": False, "need_login": True, "error": "公众号未登录，请先扫码登录"}
            token = _read_token(c)
            c.call("Page.navigate", url=EDITOR_URL.format(token=token))
            time.sleep(2.5)
            _wait_editor(c)

            _set_title(c, title)
            _set_body(c, html)
            st = _insert_images(c, image_files)

            miss = len(image_files) - int(st.get("cdn", 0))
            res = _save_draft(c)
            time.sleep(2.5)
            ok = res.get("ok") and _saved_ok(c)
            return {
                "ok": bool(ok),
                "images": int(st.get("imgs", 0)),
                "cdn": int(st.get("cdn", 0)),
                "missing": max(0, miss),
                "warn": f"{miss} 张图未转存，草稿可能裂图" if miss > 0 else "",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            c.close()
