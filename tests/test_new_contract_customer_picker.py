"""新建合同的客户搜索：已锁定客户不能从下拉里消失。

线上反馈（2026-11-25）：新建合同时输入已录入客户的姓名 / 手机号 / 房号，
**一个都搜不到**。查下来的原因不是搜索坏了 ——
`new_contract_form` 把 `c.locked` 的客户整体过滤掉了，而线上 11 个客户
全部 `locked=1`（前期施工完工自动锁定 + 管理员手动锁过一批），于是下拉是空的。

锁定是「提醒已开工、别选错人」，不是「禁止再签」：同一客户先地暖后暖气片、
分期签第二份合同是正常业务。所以现在保留在下拉里、排到未锁定之后、带 locked
标记，前端选中时二次确认。
"""
from __future__ import annotations

import json
import re

from starlette.requests import Request

from app.models import Customer
from app.routers import contracts as contracts_router


def _req(path: str = "/contracts/new") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 5555),
            "server": ("testserver", 80),
            "scheme": "http",
            "root_path": "",
        }
    )


def _seed(db, admin):
    open_c = Customer(name="新客户甲", phone="13900000001", owner_id=admin.id)
    locked_c = Customer(name="老客户乙", phone="13900000002", locked=1, owner_id=admin.id)
    db.add_all([open_c, locked_c])
    db.flush()
    return open_c, locked_c


def _options(db, admin) -> list[dict]:
    """把模板里注入的 customer_options 取回来。

    Jinja 的 tojson 会把中文转成 \\uXXXX（ensure_ascii），所以不能直接断言中文
    出现在 HTML 里 —— 解析回 JSON 再比较。
    """
    resp = contracts_router.new_contract_form(
        request=_req(), customer_id="", db=db, user=admin
    )
    html = resp.body.decode()
    m = re.search(r"var data = (\[.*?\]) \|\|", html, re.S)
    assert m, "模板里没找到 customer_options 的注入点"
    return json.loads(m.group(1))


def test_locked_customer_still_in_options(db, admin):
    _seed(db, admin)
    names = [c["name"] for c in _options(db, admin)]
    assert "新客户甲" in names and "老客户乙" in names, "锁定客户必须留在下拉里，否则搜不到人"


def test_locked_customer_flagged_and_sorted_last(db, admin):
    _seed(db, admin)
    opts = _options(db, admin)
    by_name = {c["name"]: c for c in opts}
    assert by_name["老客户乙"]["locked"] == 1, "锁定标记要传给前端，选中时才能二次确认"
    assert by_name["新客户甲"]["locked"] == 0
    assert opts[-1]["name"] == "老客户乙", "锁定的排后面，降低误选"


def test_room_and_phone_are_searchable_fields(db, admin):
    """搜索走前端本地过滤：房号（room）与手机号必须真的进到 data 里。"""
    _seed(db, admin)
    opts = _options(db, admin)
    assert opts[0]["phone"] == "13900000001"
    assert "room" in opts[0], "缺 room 字段，输入房号就搜不到"
