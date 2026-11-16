"""统一的 404 出口。

为什么单独抽一个模块
------------------
「路由不存在」和「被安全入口拦住」是两条完全不同的代码路径：

- 路由不存在 → Starlette 抛 `HTTPException` → 由 FastAPI **内置**的
  `http_exception_handler` 返回 `{"detail":"Not Found"}`（JSON）；
- 安全入口 → 在认证中间件里就 `return` 掉了，根本没进路由层，也碰不到任何
  异常处理器。

两者必须给出**逐字节一致**的响应。差异本身就是指纹——扫描器只要对比
「`/persons`」和「`/s/瞎猜的字符串`」的响应体/状态码/头部不同，就能确认
「这台机器上藏着一个真系统」，门牌的隐蔽性直接归零。

所以两条路径都从这里出。同时把裸 JSON 换成一张**无指纹**的静态 404 页面：

- 纯前端 404 本来就更「普通」（nginx/Apache 的默认 404 也是 HTML），
  裸 JSON 反而泄露了技术栈（FastAPI/Starlette 的固定文案）；
- 页面内不引用 `/static/*`、不含产品名、不用模板变量、无 JS，
  加载失败也不会露出任何还会响应的资源。

`/api/*` 仍返回 JSON：调用方（前端 fetch / 小程序）需要结构化错误。
"""
from __future__ import annotations

from pathlib import Path

from fastapi.responses import HTMLResponse, JSONResponse, Response

API_PREFIX = "/api"
NOT_FOUND_DETAIL = "Not Found"

_TEMPLATE = Path(__file__).resolve().parent / "templates" / "bare404.html"
_html_cache: str | None = None


def not_found_html() -> str:
    """读取静态 404 页面（进程内缓存一次，改动需重启容器）。"""
    global _html_cache
    if _html_cache is None:
        _html_cache = _TEMPLATE.read_text(encoding="utf-8")
    return _html_cache


def is_api(path: str) -> bool:
    return path == API_PREFIX or path.startswith(API_PREFIX + "/")


def not_found(path: str = "") -> Response:
    """404 响应：`/api/*` 走 JSON（与 FastAPI 内置处理器一致），其余走静态页面。"""
    if is_api(path):
        # 不加任何额外头部：必须和 FastAPI 内置 http_exception_handler 的输出一模一样
        return JSONResponse({"detail": NOT_FOUND_DETAIL}, status_code=404)
    return HTMLResponse(
        not_found_html(),
        status_code=404,
        # 不许缓存：入口字符串改对之后，浏览器不能还端着上一次的 404
        headers={"Cache-Control": "no-store"},
    )
