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
