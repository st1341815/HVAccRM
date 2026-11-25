"""电子合同 / 结算单打印页（`/contracts/{id}/print/*`）的端到端约定。

这两条路由以前没有测试，也踩过一次坑：模板里取公司抬头用的是
`company_info(db).get("甲方名称")`，而 settings 的 key 其实是 `doc.company_name`
—— 取了个不存在的键，纸上「乙方」永远是空的，静态检查看不出来。

所以这里直调路由函数真跑一遍，钉住：
1. 电子合同：模板正文填得上数据（客户名 / 公司抬头 / 明细）；
2. 结算单：**没标记结算**就拒绝（409 + 说明原因），不是悄悄打一张未结算的单子；
3. 结算单客户版（`hide_cost=1`）不含成本与毛利，内部版才有。
"""
from __future__ import annotations

from starlette.requests import Request

from app.models import Contract, ContractCost, ContractItem, Customer, Payment
from app.routers import contracts as contracts_router
from app.services import contract_docs as doc_svc


def _req(path: str = "/contracts/1/print/contract") -> Request:
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


def _seed(db, admin, *, settled: int = 0) -> Contract:
    c = Customer(name="张三", phone="13800000000", type="家装", address="苏州工业园区某小区 3-1801", owner_id=admin.id)
    db.add(c)
    db.flush()
    ctr = Contract(
        customer_id=c.id, no="HT-2026-001", product_type="中央空调", total_amount=10000,
        sign_date="2026-05-01", status="active", settled=settled,
        settled_at="2026-06-01" if settled else None, created_by=admin.id,
    )
    db.add(ctr)
    db.flush()
    db.add(ContractItem(contract_id=ctr.id, product_name="大金中央空调", spec="5P 一拖四",
                        qty=1, unit="套", unit_price=10000, amount=10000))
    db.add(Payment(contract_id=ctr.id, paid_at="2026-05-02", amount=6000, method="转账",
                   voucher_no="P-1", received_by=admin.id))
    db.add(ContractCost(contract_id=ctr.id, spent_at="2026-05-10", category="材料",
                        remark="铜管采购", amount=3000))
    db.flush()
    return ctr


def test_print_contract_fills_template(db, admin):
    ctr = _seed(db, admin)
    doc_svc.save_company_info(db, {"doc.company_name": "苏州某某暖通工程有限公司"})
    db.flush()

    resp = contracts_router.print_contract_doc(
        request=_req(), contract_id=ctr.id, tpl=None, db=db, user=admin
    )
    assert resp.status_code == 200
    html = resp.body.decode()
    assert "苏州某某暖通工程有限公司" in html, "公司抬头没填上（settings key 用错就会这样）"
    assert "张三" in html
    assert "大金中央空调" in html and "10,000.00" in html
    assert "HT-2026-001" in html


def test_print_contract_seeds_template_on_empty_db(db, admin):
    ctr = _seed(db, admin)
    resp = contracts_router.print_contract_doc(
        request=_req(), contract_id=ctr.id, tpl=None, db=db, user=admin
    )
    assert resp.status_code == 200
    assert doc_svc.default_template(db) is not None, "空库打印时必须落地内置范本，不能给白页"


def test_print_settlement_blocked_when_not_settled(db, admin):
    ctr = _seed(db, admin, settled=0)
    resp = contracts_router.print_settlement_doc(
        request=_req(), contract_id=ctr.id, hide_cost=0, db=db, user=admin
    )
    assert resp.status_code == 409
    assert "还没标记结算" in resp.body.decode()


def _paper(html: str) -> str:
    """只取「纸上」那部分：工具条在 @media print 里不显示，别拿它的文案当正文断言。"""
    return html[html.index('<div class="doc-page">'):]


def test_print_settlement_internal_vs_customer_copy(db, admin):
    ctr = _seed(db, admin, settled=1)

    internal = contracts_router.print_settlement_doc(
        request=_req(), contract_id=ctr.id, hide_cost=0, db=db, user=admin
    )
    assert internal.status_code == 200
    paper = _paper(internal.body.decode())
    assert "成本明细" in paper and "铜管采购" in paper and "毛利" in paper
    assert "￥10,000.00" in paper
    assert "壹万元整" in paper, "应收总额大写应按应收（含增项）取，不是按合同金额"

    customer = contracts_router.print_settlement_doc(
        request=_req(), contract_id=ctr.id, hide_cost=1, db=db, user=admin
    )
    assert customer.status_code == 200
    cpaper = _paper(customer.body.decode())
    assert "成本明细" not in cpaper and "铜管采购" not in cpaper and "毛利" not in cpaper
    assert "张三" in cpaper and "大金中央空调" in cpaper
