"""结算 / 取消结算的确认页（v1.15.10）。

**背景**：`标记结算` 原来是 `onsubmit="return confirm(...)"` —— 浏览器原生弹窗，
字体、按钮、位置全是浏览器的，跟系统没有关系，也带不上合同上下文（客户是谁、
金额多少、为什么不能结算）。而同一个界面上「未结算点结算单」看到的是系统内的
拦截页（`print_blocked.html`），两种提示并存很割裂。

现在两边统一：点「标记结算」进系统内确认页，确认后再 POST。
这里钉住三件事：

1. 页面真的渲染出来（标题、合同号、确认按钮的 action）；
2. **不能结算时不显示确认按钮**，把原因说清楚——不能让人点完才报错；
3. 模板里不许再有结算相关的 `confirm()`（防止以后改回去）。
"""
from __future__ import annotations

import re
from pathlib import Path

from starlette.requests import Request

from app.models import Contract, Customer
from app.routers import contracts as contracts_router

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "app" / "templates"


def _req(path: str = "/contracts/1/settle") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "scheme": "http",
            "server": ("127.0.0.1", 8090),
            "client": ("10.0.0.9", 52341),
            "headers": [(b"user-agent", b"pytest")],
            "query_string": b"",
        }
    )


def _contract(db, admin, *, settled: int = 0, status: str = "active", no: str = "HT-SET-1") -> Contract:
    c = Customer(name="张三", phone="13800000000", owner_id=admin.id, status="won")
    db.add(c)
    db.flush()
    ctr = Contract(
        customer_id=c.id,
        no=no,
        product_type="中央空调",
        total_amount=10000,
        sign_date="2026-05-01",
        status=status,
        settled=settled,
        settled_at="2026-06-01" if settled else None,
        created_by=admin.id,
    )
    db.add(ctr)
    db.flush()
    return ctr


def _settle_page(db, admin, contract_id: int, back: str = ""):
    return contracts_router.settle_confirm(
        request=_req(), contract_id=contract_id, back=back, db=db, user=admin
    )


def _unsettle_page(db, admin, contract_id: int, back: str = ""):
    return contracts_router.unsettle_confirm(
        request=_req(), contract_id=contract_id, back=back, db=db, user=admin
    )


def test_settle_confirm_page_renders_with_context(db, admin):
    """能结算时：系统内页面，带合同号 / 客户名 / 影响说明 / 确认按钮。"""
    ctr = _contract(db, admin, no="HT-SET-10")
    resp = _settle_page(db, admin, ctr.id)
    assert resp.status_code == 200, "应该渲染确认页而不是直接跳走"
    html = bytes(resp.body).decode()

    assert "标记结算" in html
    assert "HT-SET-10" in html, "确认页必须带合同号，否则不知道在给哪张单子关账"
    assert "张三" in html, "带上客户名"
    assert "冻结" in html, "要说清标记结算的后果"
    assert f'action="/contracts/{ctr.id}/settle"' in html, "确认按钮应 POST 到 settle"
    assert 'name="csrf_token"' in html, "POST 必须带 CSRF token"
    assert "确认标记结算" in html


def test_settle_confirm_shows_blocker_and_hides_button(db, admin):
    """有未完成工序时：把原因写在页面上，且不显示确认按钮。

    `settlement_svc.can_settle` 会拦「作废合同」和「还有工序没完成」。
    以前是点完 POST 才弹错误，现在进来就知道。
    """
    ctr = _contract(db, admin, no="HT-SET-11")
    from app.models import Task

    db.add(
        Task(
            customer_id=ctr.customer_id,
            contract_id=ctr.id,
            stage="室内机吊装",
            sort_order=1,
            status="doing",
        )
    )
    db.flush()

    html = bytes(_settle_page(db, admin, ctr.id).body).decode()
    assert "未完成" in html, "要把「还有几道工序没完成」写在页面上"
    assert f'action="/contracts/{ctr.id}/settle"' not in html, (
        "不能结算时不该给确认按钮——点了也是白点"
    )
    assert "确认标记结算" not in html


def test_settle_confirm_on_void_contract(db, admin):
    """作废合同同样只显示原因，不给按钮。"""
    ctr = _contract(db, admin, status="void", no="HT-SET-12")
    html = bytes(_settle_page(db, admin, ctr.id).body).decode()
    assert "作废" in html
    assert "确认标记结算" not in html


def test_settle_confirm_redirects_when_already_settled(db, admin):
    """已经结算过的合同，点「标记结算」直接回列表并提示，不再渲染确认页。"""
    ctr = _contract(db, admin, settled=1, no="HT-SET-13")
    resp = _settle_page(db, admin, ctr.id)
    assert resp.status_code in (302, 303), "已结算应直接重定向"
    assert resp.headers.get("location") == "/contracts", "应回到合同列表"
    # 原因走 flash（cookie），不在 URL 里——别把中文提示塞进 location
    assert "crm_session_flash" in resp.headers.get("set-cookie", ""), "重定向要带上提示"


def test_unsettle_confirm_page_renders(db, admin):
    """取消结算同样走系统内确认页。"""
    ctr = _contract(db, admin, settled=1, no="HT-SET-14")
    resp = _unsettle_page(db, admin, ctr.id)
    assert resp.status_code == 200
    html = bytes(resp.body).decode()
    assert "取消结算" in html
    assert "HT-SET-14" in html
    assert f'action="/contracts/{ctr.id}/unsettle"' in html
    assert "确认取消结算" in html


def test_unsettle_confirm_redirects_when_not_settled(db, admin):
    ctr = _contract(db, admin, no="HT-SET-15")
    resp = _unsettle_page(db, admin, ctr.id)
    assert resp.status_code in (302, 303)
    assert "crm_session_flash" in resp.headers.get("set-cookie", ""), "重定向要带上提示"


def test_confirm_page_back_link_is_site_relative(db, admin):
    """`back` 只接受站内相对路径，挡住开放重定向。"""
    ctr = _contract(db, admin, no="HT-SET-16")
    html = bytes(_settle_page(db, admin, ctr.id, back="https://evil.example/x").body).decode()
    assert "evil.example" not in html, "外部地址不该被当成回跳目标"
    assert f"/contracts/{ctr.id}" in html


def test_contract_templates_no_longer_use_confirm_for_settle():
    """模板静态检查：结算相关不许再回到浏览器 confirm()。

    别的删除类操作仍在用 confirm（那是另一回事，本次没动），所以只针对
    `/settle` / `/unsettle` 这两个动作断言。
    """
    for name in ("contracts/detail.html", "contracts/list.html"):
        src = (TPL / name).read_text(encoding="utf-8")
        assert "/settle" in src, f"{name} 应该还留着结算入口链接"
        # 结算那一段不许再出现 form + confirm 的组合
        settle_blocks = re.findall(r"<form[^>]*/(?:un)?settle[^>]*>", src)
        assert not settle_blocks, f"{name} 里还有 POST 表单直连结算动作（应改走确认页）"
        assert "确认把该合同标记为结算" not in src, f"{name} 里还留着旧的 confirm 文案"
        assert "确认取消该合同的结算" not in src, f"{name} 里还留着旧的 confirm 文案"
