"""收款管理：实收流水、超额拦截、退款登记、逾期视图。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import log_action
from ..auth import current_user_or_redirect
from ..db import commit_retry, get_db
from ..models import Contract, Payment, User, now_iso, today_str
from ..permissions import has_perm, require, visible_customer_ids
from ..services import finance as finance_svc
from ..templating import redirect, render
from ..utils import client_ip, get_or_404, parse_float, parse_int

router = APIRouter(prefix="/payments", tags=["payments"])

METHODS = ["微信", "支付宝", "银行转账", "刷卡", "现金", "对公转账", "其他"]


@router.get("")
def list_payments(
    request: Request,
    contract_id: str = "",
    customer_id: str = "",
    date_from: str = "",
    date_to: str = "",
    method: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require("payment:view")),
):
    stmt = select(Payment).order_by(Payment.paid_at.desc(), Payment.id.desc())
    if contract_id:
        stmt = stmt.where(Payment.contract_id == parse_int(contract_id))
    if date_from:
        stmt = stmt.where(Payment.paid_at >= date_from)
    if date_to:
        stmt = stmt.where(Payment.paid_at <= date_to)
    if method:
        stmt = stmt.where(Payment.method == method)
    payments = list(db.scalars(stmt).all())

    contract_map = {c.id: c for c in db.scalars(select(Contract)).all()}
    allowed = visible_customer_ids(db, user)
    if allowed is not None:
        payments = [p for p in payments if contract_map.get(p.contract_id) and contract_map[p.contract_id].customer_id in allowed]
    if customer_id:
        payments = [
            p for p in payments if contract_map.get(p.contract_id) and str(contract_map[p.contract_id].customer_id) == customer_id
        ]

    users = {u.id: u for u in db.scalars(select(User)).all()}
    total = round(sum(p.amount or 0 for p in payments), 2)
    month = today_str()[:7]
    month_total = round(sum(p.amount or 0 for p in payments if (p.paid_at or "").startswith(month)), 2)
    refund_total = round(sum(-(p.amount or 0) for p in payments if (p.amount or 0) < 0), 2)
    return render(
        request,
        "payments/list.html",
        payments=payments,
        contract_map=contract_map,
        users=users,
        total=total,
        month_total=month_total,
        refund_total=refund_total,
        filters={
            "contract_id": contract_id,
            "customer_id": customer_id,
            "date_from": date_from,
            "date_to": date_to,
            "method": method,
        },
        methods=METHODS,
        can_edit=has_perm(user, "payment:edit"),
        can_refund=has_perm(user, "payment:refund"),
        can_delete=has_perm(user, "admin:setting"),
        today=today_str(),
    )


@router.get("/overdue")
def overdue_view(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("payment:view")),
):
    finance_svc.refresh_all(db)
    contracts = list(db.scalars(select(Contract)).all())
    allowed = visible_customer_ids(db, user)
    rows = []
    for c in contracts:
        if allowed is not None and c.customer_id not in allowed:
            continue
        s = finance_svc.contract_summary(db, c)
        if s.overdue > finance_svc.TOL or s.outstanding > finance_svc.TOL:
            rows.append((c, s))
    rows.sort(key=lambda r: r[1].overdue, reverse=True)
    debtors = finance_svc.top_debtors(db, 5)
    return render(
        request,
        "payments/overdue.html",
        rows=rows,
        debtors=debtors,
        total_overdue=round(sum(s.overdue for _, s in rows), 2),
        total_outstanding=round(sum(s.outstanding for _, s in rows), 2),
        today=today_str(),
    )


@router.post("/record")
def record_payment(
    request: Request,
    contract_id: str = Form(...),
    amount: str = Form(...),
    paid_at: str = Form(""),
    method: str = Form(""),
    voucher_no: str = Form(""),
    remark: str = Form(""),
    back: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require("payment:edit")),
):
    contract = db.get(Contract, parse_int(contract_id))
    if not contract:
        return redirect("/payments", "合同不存在", "err")
    amt = parse_float(amount)
    is_refund = amt < 0
    if is_refund and not has_perm(user, "payment:refund"):
        return render(request, "403.html", status_code=403)
    summary = finance_svc.contract_summary(db, contract)
    problem = finance_svc.check_payment(contract, summary, amt, is_refund=is_refund)
    if problem:
        target = back or f"/contracts/{contract.id}"
        return redirect(target, problem, "err")

    payment = Payment(
        contract_id=contract.id,
        amount=amt,
        paid_at=paid_at.strip() or today_str(),
        method=method.strip() or None,
        voucher_no=voucher_no.strip() or None,
        received_by=user.id,
        remark=remark.strip() or None,
        created_at=now_iso(),
    )
    db.add(payment)
    db.flush()
    finance_svc.allocate_plans(db, contract)
    log_action(
        db,
        user,
        "refund" if is_refund else "create",
        "payments",
        payment.id,
        new=payment,
        ip=client_ip(request),
    )
    commit_retry(db)
    verb = "退款登记" if is_refund else "收款登记"
    return redirect(back or f"/contracts/{contract.id}", f"{verb}成功：{amt:,.2f}")


@router.post("/{payment_id}/delete")
def delete_payment(
    request: Request,
    payment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require("admin:setting")),
):
    payment = get_or_404(db, Payment, payment_id, "收款记录")
    contract = db.get(Contract, payment.contract_id)
    log_action(db, user, "delete", "payments", payment.id, old=payment, ip=client_ip(request))
    db.delete(payment)
    db.flush()
    if contract:
        finance_svc.allocate_plans(db, contract)
    commit_retry(db)
    return redirect(f"/contracts/{payment.contract_id}", "收款记录已删除（已重算应收）")
