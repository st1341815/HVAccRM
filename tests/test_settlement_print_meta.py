"""结算单（`/contracts/{id}/print/settlement`）v1.15.0 的三处改动。

用户提的三条，逐条钉住：

1. **工程地址是「-」**：模板取 `customer.address`，但客户表单里「地址」选填、房号必填
   （线上 11 个客户地址全空、房号 11 个都有），所以纸面上永远是横杠。
   改成和电子合同同一个口径：市+区+楼盘+楼栋+单元+房号。
2. **文末甲乙方签字栏**：结算单不再走线下签字，删掉。
3. **打印时间 / 打印人 / 打印次数**：每次生成结算单往 `audit_log` 写一条
   `settlement_print`，序号印在纸面上，重打留底、纸和系统能对上。
"""
from __future__ import annotations

import re

from starlette.requests import Request

from app.models import (
    AuditLog,
    Contract,
    Customer,
    Project,
    Room,
)
from app.routers import contracts as contracts_router


def _req(path: str = "/contracts/1/print/settlement") -> Request:
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


def _settled_contract(db, admin, *, with_room: bool = True, no: str = "HT-PRINT-1") -> Contract:
    """已标记结算的合同。with_room=True 时客户挂到楼盘房号上，address 故意留空。"""
    c = Customer(name="张三", phone="13800000000", owner_id=admin.id, status="won")
    if with_room:
        proj = Project(name="翡翠花园", city="合肥", district="蜀山区")
        db.add(proj)
        db.flush()
        room = Room(project_id=proj.id, building="6栋", unit="1单元", room_no="401")
        db.add(room)
        db.flush()
        c.room_id = room.id
    db.add(c)
    db.flush()
    ctr = Contract(
        customer_id=c.id,
        no=no,
        product_type="中央空调",
        total_amount=10000,
        sign_date="2026-05-01",
        status="active",
        settled=1,
        settled_at="2026-06-01",
        created_by=admin.id,
    )
    db.add(ctr)
    db.flush()
    return ctr


def _print(db, admin, contract_id: int, hide_cost: int = 0):
    return contracts_router.print_settlement_doc(
        request=_req(), contract_id=contract_id, hide_cost=hide_cost, db=db, user=admin
    )


def test_site_address_uses_room_not_empty_address(db, admin):
    """工程地址取「市+区+楼盘+楼栋+单元+房号」，不再是一根横杠。"""
    ctr = _settled_contract(db, admin)
    html = _print(db, admin, ctr.id).body.decode()
    assert "合肥市蜀山区翡翠花园 6栋1单元401" in html, "工程地址没有拼房号"
    assert '<span class="lbl">工程地址：</span>-' not in html, "工程地址不该再是横杠"


def test_site_address_falls_back_to_manual_address(db, admin):
    """没挂房号的客户，退回手填地址（别把老数据弄丢）。"""
    ctr = _settled_contract(db, admin, with_room=False, no="HT-PRINT-2")
    customer = db.get(Customer, ctr.customer_id)
    customer.address = "芜湖市镜湖区某小区 2-303"
    db.flush()
    html = _print(db, admin, ctr.id).body.decode()
    assert "芜湖市镜湖区某小区 2-303" in html


def test_no_signature_block(db, admin):
    """文末不再有甲乙方签字 / 盖章栏。"""
    ctr = _settled_contract(db, admin, no="HT-PRINT-3")
    html = _print(db, admin, ctr.id).body.decode()
    assert "甲方（签字 / 盖章）" not in html, "结算单不该再有甲方签字栏"
    assert "乙方（签字 / 盖章）" not in html, "结算单不该再有乙方签字栏"
    assert "doc-sign" not in html, "签字区样式块应一并移除"
    assert "经双方确认签字后" not in html, "条款里「签字」二字也要改（已无签字栏）"
    assert "经双方确认后" in html


def test_print_seq_and_user_are_recorded(db, admin):
    """每次打印都留痕：纸面显示第 N 次 + 时间 + 打印人，库里也真有一条。"""
    ctr = _settled_contract(db, admin, no="HT-PRINT-4")

    first = _print(db, admin, ctr.id).body.decode()
    assert "第 1 次打印" in first, "第一次打印应标「第 1 次」"
    assert f"打印人：{admin.display_name}" in first, "纸面要有打印人"

    second = _print(db, admin, ctr.id).body.decode()
    assert "第 2 次打印" in second, "第二次打印应标「第 2 次」"

    logs = (
        db.query(AuditLog)
        .filter(
            AuditLog.action == "settlement_print",
            AuditLog.table_name == "contracts",
            AuditLog.record_id == ctr.id,
        )
        .order_by(AuditLog.id)
        .all()
    )
    assert len(logs) == 2, f"应留下 2 条打印记录，实际 {len(logs)}"
    assert logs[0].username == admin.username
    assert logs[0].ip == "10.0.0.9", "打印记录要带 IP，不然出了事查不到来源"
    assert '"seq": 1' in (logs[0].new_val or ""), "记录里要存本次序号"


def test_print_seq_is_per_contract(db, admin):
    """次数按合同各算各的，不是全局累加。"""
    a = _settled_contract(db, admin, no="HT-PRINT-A")
    b = _settled_contract(db, admin, no="HT-PRINT-B")
    _print(db, admin, a.id)
    _print(db, admin, a.id)
    html_b = _print(db, admin, b.id).body.decode()
    assert "第 1 次打印" in html_b, "另一份合同的次数应从头算"


# ---------------------------------------------------------------- v1.15.3
def test_no_contractor_line_in_header(db, admin):
    """抬头里不再有「乙方（承包方）」——结算单是给客户看的财务凭证，
    承包主体是谁对这张单子没有意义，占一行还挤掉了别的信息。"""
    ctr = _settled_contract(db, admin, no="HT-PRINT-4")
    html = _print(db, admin, ctr.id).body.decode()
    assert "乙方（承包方）" not in html, "抬头里的乙方那行应已删除"
    assert "乙方" not in html, "结算单全文不该再出现「乙方」"


HEADER_ORDER = ["工程地址：", "结算日期：", "签订日期：", "销售经理：",
                "产品类型：", "甲方名称：", "联系电话："]


def _parties_block(html: str) -> str:
    m = re.search(r'<div class="doc-parties">(.*?)</div>\s*<h2', html, re.S)
    assert m, "找不到抬头信息区（doc-parties）"
    return m.group(1)


def test_sales_name_in_header(db, admin):
    """销售经理在抬头信息区，= 录入人（合同 created_by）。

    v1.15.3 先放在文末（顶替签字栏位置），v1.15.4 挪回抬头，
    v1.15.9 按用户给的版式重排并改称「销售经理」。
    """
    ctr = _settled_contract(db, admin, no="HT-PRINT-5")
    html = _print(db, admin, ctr.id).body.decode()

    block = _parties_block(html)
    assert "销售经理：" in block, "销售经理应放在抬头信息区里"
    assert 'class="doc-foot-info"' not in html, "文末不该再留独立的落款块"

    m = re.search(r'<span class="lbl">销售经理：</span>([^<]*)', html)
    assert m, "抬头里没有「销售经理」这一行"
    assert m.group(1).strip() == admin.display_name, (
        f"销售经理应取录入人 {admin.display_name}，实际 {m.group(1).strip()!r}"
    )


def test_header_layout_follows_user_spec(db, admin):
    """v1.15.9 用户指定的抬头版式：工程地址独占一行，其余两两成对。

        工程地址：***
        结算日期：***   签订日期：***
        销售经理：***   产品类型：***
        甲方名称：***   联系电话：***

    工程地址必须带 `.wide`（CSS 里 `grid-column: 1 / -1`）——地址是「市+区+楼盘+
    楼栋+单元+房号」，塞进半栏会折成两行，把整块顶高。
    「结算状态」不再是独立一格：它和结算日期是同一件事，未结算时在日期后括号标出，
    抬头因此正好 4 行排满，不留孤零零的半个空格。
    """
    ctr = _settled_contract(db, admin, no="HT-PRINT-7")
    html = _print(db, admin, ctr.id).body.decode()
    block = _parties_block(html)

    positions = [block.index(k) for k in HEADER_ORDER]
    assert positions == sorted(positions), (
        "抬头字段顺序不对，期望：" + " → ".join(k.rstrip("：") for k in HEADER_ORDER)
    )

    wide = re.search(r'<div class="wide"><span class="lbl">([^<]*)</span>', block)
    assert wide and wide.group(1) == "工程地址：", (
        "只有工程地址该独占整行（class=\"wide\"），其余两两成对"
    )
    assert block.count('class="wide"') == 1, "独占整行的字段只应有一个"

    assert "结算状态：" not in block, "结算状态已并入结算日期，不该再单独占一格"


def test_settled_contract_has_no_unsettled_suffix(db, admin):
    """已结算的单子，结算日期后面不该挂「（未结算）」。"""
    ctr = _settled_contract(db, admin, no="HT-PRINT-8")
    html = _print(db, admin, ctr.id).body.decode()
    m = re.search(r'<span class="lbl">结算日期：</span>([^<]*)', html)
    assert m, "抬头里没有「结算日期」这一行"
    assert "未结算" not in m.group(1), f"已结算的单子不该标未结算：{m.group(1)!r}"




def test_sales_name_falls_back_to_customer_owner(db, admin, sales):
    """合同没记 created_by 的老数据，退回客户负责人——别印成一根横杠。

    口径必须和电子合同的 {{业务员}} 一致（contract_docs.build_context），
    否则同一单在合同和结算单上会写出两个人。
    """
    ctr = _settled_contract(db, admin, no="HT-PRINT-6")
    ctr.created_by = None
    db.get(Customer, ctr.customer_id).owner_id = sales.id
    db.flush()
    html = _print(db, admin, ctr.id).body.decode()
    m = re.search(r'<span class="lbl">销售经理：</span>([^<]*)', html)
    assert m and m.group(1).strip() == sales.display_name, "没填录入人时应回落客户负责人"
