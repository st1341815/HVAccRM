"""CSRF 保护：只用于「高危 POST」（登录 / 用户管理 / 账户安全 / 备份操作）。

设计取舍：
- 不引第三方库，复用 `app/security.py` 的 HMAC 自签 token（供应链面不变）；
- 双提交：Cookie 里存 `<nonce>.<hmac>`，表单里回填同一个值，两者相等且签名有效才算过；
- 只保护高危路由而不是全站中间件 —— 中间件要读 body（备份导入是大文件）且容易把
  登录页、HTMX 局部请求一起打死，收益/风险不划算。
"""
from __future__ import annotations

from fastapi import Form, HTTPException, Request

from .security import verify_csrf
from .templating import csrf_cookie_name


def csrf_protect(request: Request, csrf_token: str = Form("")) -> None:
    """FastAPI 依赖：校验表单里的 csrf_token 与 Cookie 是否一致。"""
    if not verify_csrf(request.cookies.get(csrf_cookie_name()), csrf_token):
        raise HTTPException(status_code=403, detail="页面已过期或来源不可信，请刷新后重试")
