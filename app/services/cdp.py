"""极简 CDP（Chrome DevTools Protocol）客户端。

为什么不引 playwright：
  容器基础镜像只有 ~185MB，playwright 要额外 ~500MB（含它自带的 chromium
  和一堆系统库，且 ``playwright install --with-deps`` 在 Debian 13 上会卡死）。
  
  我们只需要三件事——截图、执行 JS、给 file input 喂文件——自己连 CDP 就够了。

关键坑（都实测过）：
  1. 连接必须 ``suppress_origin=True``：Chrome 会拒掉带 Origin 头的 CDP 连接（403），
     除非启动时给了 ``--remote-allow-origins``。
  2. 图片上传只能靠 ``DOM.setFileInputFiles``：浏览器安全限制无法用 JS 伪造 File。
"""
from __future__ import annotations

import json
import urllib.request

import websocket


class CDPError(RuntimeError):
    pass


def targets(port: int) -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as r:
        return json.load(r)


def page_ws(port: int, match: str = "mp.weixin.qq.com") -> str | None:
    """找地址里含 match 的 page target。没有匹配则取第一个 page。"""
    pages = [t for t in targets(port) if t.get("type") == "page"]
    for t in pages:
        if match in (t.get("url") or ""):
            return t["webSocketDebuggerUrl"]
    return pages[0]["webSocketDebuggerUrl"] if pages else None


class CDP:
    def __init__(self, ws_url: str, timeout: int = 60):
        self.ws = websocket.create_connection(ws_url, timeout=timeout, suppress_origin=True)
        self._id = 0

    def call(self, method: str, **params):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise CDPError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expression: str, await_promise: bool = False):
        res = self.call(
            "Runtime.evaluate",
            expression=expression,
            returnByValue=True,
            awaitPromise=await_promise,
        )
        if res.get("exceptionDetails"):
            d = res["exceptionDetails"]
            desc = d.get("exception", {}).get("description") or d.get("text")
            raise CDPError(f"JS 异常：{desc}")
        return res.get("result", {}).get("value")

    def node_id(self, selector: str) -> int:
        root = self.call("DOM.getDocument")["root"]["nodeId"]
        return self.call("DOM.querySelector", nodeId=root, selector=selector)["nodeId"]

    def set_files(self, selector: str, files: list[str]) -> None:
        nid = self.node_id(selector)
        if not nid:
            raise CDPError(f"没找到 {selector}")
        self.call("DOM.setFileInputFiles", files=list(files), nodeId=nid)

    def screenshot_png(self, quality_scale: float = 1.0) -> bytes:
        """整页截图（PNG base64 解码）。用于给用户扫二维码。"""
        import base64

        r = self.call("Page.captureScreenshot", format="png", captureBeyondViewport=False)
        return base64.b64decode(r["data"])

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass
