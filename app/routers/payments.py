"""收款管理：实收流水、超额拦截、退款登记、逾期视图。"""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import log_action
from ..auth import current_user_or_redirect
from ..db import commit_retry, get_db
from ..models import Contract, Payment, User, now_iso, today_str
from ..permissions import has_perm, require, visible_customer_ids
from ..services import finance as finance_svc
from ..services import photos as photo_svc
from ..services import search as search_svc
from ..services import settlement as settlement_svc
from ..templating import redirect, render
from ..utils import alnum_sort_key, client_ip, get_or_404, parse_float, parse_int

router = APIRouter(prefix="/payments", tags=["payments"])

METHODS = ["微信", "支付宝", "银行转账", "刷卡", "现金", "对公转账", "其他"]
PAYMENT_PHOTO_KIND = "收款截图"


@router.get("")
def list_payments(
    request: Request,
    contract_q: str = "",
    customer_q: str = "",
    date_from: str = "",
    date_to: str = "",
    method: str = "",
    sort: str = "paid_at",
    order: str = "desc",
    db: Session = Depends(get_db),
    user: User = Depends(require("payment:view")),
):
    stmt = select(Payment).order_by(Payment.paid_at.desc(), Payment.id.desc())
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
    # 合同号关键字过滤（如 ONE2026…）
    if contract_q and contract_q.strip():
        q = contract_q.strip()
        payments = [
            p for p in payments
            if contract_map.get(p.contract_id) and q in (contract_map[p.contract_id].no or "")
        ]
    # 客户关键字过滤：房号 / 姓名 / 手机号 / 楼盘名
    if customer_q and customer_q.strip():
        cids = set(search_svc.customer_ids_by_keyword(db, customer_q))
        payments = [
            p for p in payments
            if contract_map.get(p.contract_id) and contract_map[p.contract_id].customer_id in cids
        ]

    users = {u.id: u for u in db.scalars(select(User)).all()}

    # ---- 排序（内存里做：房号跨表取 customer 的 property、收款人是另一张表里的中文名）----
    sort_options = [
        ("paid_at", "收款日期"),
        ("amount", "金额"),
        ("room", "房号"),
        ("method", "方式"),
        ("voucher_no", "凭证号"),
        ("received_by", "收款人"),
        ("remark", "备注"),
    ]
    if sort not in dict(sort_options):
        sort = "paid_at"
        order = "desc"  # 字段都非法了，方向一起回默认，别让 ?sort=xx&order=asc 排成升序
    order = "desc" if order == "desc" else "asc"

    def _room_of(p):
        c = contract_map.get(p.contract_id)
        if not c or not c.customer:
            return alnum_sort_key("")
        return alnum_sort_key(c.customer.room_label_short or c.customer.name or "")

    def _user_name(uid):
        u = users.get(uid) if uid else None
        return u.display_name if u else ""

    sort_keys = {
        "paid_at": lambda p: (p.paid_at or ""),
        "amount": lambda p: p.amount or 0,
        "room": _room_of,
        "method": lambda p: (p.method or ""),
        # 凭证号是「字母 + 序号」，纯字符串会把 P10 排到 P6 前面，走自然排序
        "voucher_no": lambda p: alnum_sort_key(p.voucher_no or ""),
        "received_by": lambda p: _user_name(p.received_by),
        "remark": lambda p: (p.remark or ""),
    }
    payments.sort(key=sort_keys[sort], reverse=(order == "desc"))

    # 排序链接：保留现有筛选条件（点列头不丢筛选），首点 desc、再点 asc
    base_params = {
        k: v
        for k, v in {
            "contract_q": contract_q, "customer_q": customer_q,
            "date_from": date_from, "date_to": date_to, "method": method,
        }.items()
        if v
    }
    sort_links = {}
    for key, _label in sort_options:
        new_order = "asc" if (key == sort and order == "desc") else "desc"
        sort_links[key] = "/payments?" + urlencode({**base_params, "sort": key, "order": new_order})

    payment_photos = photo_svc.photos_by_payment(db, [p.id for p in payments])
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
        payment_photos=payment_photos,
        total=total,
        month_total=month_total,
        refund_total=refund_total,
        filters={
            "contract_q": contract_q,
            "customer_q": customer_q,
            "date_from": date_from,
            "date_to": date_to,
            "method": method,
        },
        sort=sort,
        order=order,
        sort_options=sort_options,
        sort_links=sort_links,
        methods=METHODS,
        can_edit=has_perm(user, "payment:edit"),
        can_refund=has_perm(user, "payment:refund"),
        can_delete=has_perm(user, "admin:setting"),
        today=today_str(),
    )


@router.get("/new")
def new_payment_form(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("payment:edit")),
):
    contracts = list(db.scalars(select(Contract).order_by(Contract.sign_date.desc(), Contract.id.desc())).all())
    allowed = visible_customer_ids(db, user)
    if allowed is not None:
        contracts = [c for c in contracts if c.customer_id in allowed]
    # 已结算合同不再出现在登记收款的选择框里（服务端另有兜底拦截）
    contracts = [c for c in contracts if not settlement_svc.is_settled(c)]
    # 合同选择改为可搜索下拉：序列化供前端本地过滤（合同号/房号/客户名）
    contract_options = [
        {
            "id": c.id,
            "no": c.no or f"#{c.id}",
            "room": c.customer.room_label_short if c.customer else "",
            "name": c.customer.name if c.customer else "",
            "product_type": c.product_type or "",
        }
        for c in contracts
    ]
    return render(
        request,
        "payments/new.html",
        contract_options=contract_options,
        methods=METHODS,
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
async def record_payment(
    request: Request,
    contract_id: str = Form(...),
    amount: str = Form(...),
    paid_at: str = Form(""),
    method: str = Form(""),
    voucher_no: str = Form(""),
    remark: str = Form(""),
    back: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(require("payment:edit")),
):
    contract = db.get(Contract, parse_int(contract_id))
    if not contract:
        return redirect("/payments", "合同不存在", "err")
    # 结算冻结：必须在写库与截图落盘之前返回
    if (frozen := settlement_svc.block_reason(contract, "payment")):
        return redirect(back or f"/contracts/{contract.id}", frozen, "err")
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
    # 付款截图：与收款记录绑定，出现在收款流水里（重复图按 Hash 去重）
    saved, dedup, errors = 0, 0, []
    for f in files:
        if not f or not f.filename:
            continue
        raw = await f.read()
        photo, msg = photo_svc.save_photo(
            db,
            customer_id=contract.customer_id,
            kind=PAYMENT_PHOTO_KIND,
            raw=raw,
            orig_name=f.filename,
            uploaded_by=user.id,
            contract_id=contract.id,
            payment_id=payment.id,
        )
        if photo is None:
            errors.append(f"{f.filename}: {msg}")
        else:
            if "重复" in msg:
                dedup += 1
            else:
                saved += 1
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
    message = f"{verb}成功：{amt:,.2f}"
    if saved or dedup:
        message += f"；付款截图新增 {saved} 张、去重 {dedup} 张"
    if errors:
        return redirect(back or f"/contracts/{contract.id}", message + "；截图失败：" + "；".join(errors), "err")
    return redirect(back or f"/contracts/{contract.id}", message)


@router.post("/{payment_id}/delete")
def delete_payment(
    request: Request,
    payment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require("admin:setting")),
):
    payment = get_or_404(db, Payment, payment_id, "收款记录")
    contract = db.get(Contract, payment.contract_id)
    if contract and (frozen := settlement_svc.block_reason(contract, "payment")):
        return redirect(f"/contracts/{contract.id}", frozen, "err")
    photo_svc.delete_photos_for(db, payment_id=payment.id)  # 先清付款截图，避免外键阻挡
    log_action(db, user, "delete", "payments", payment.id, old=payment, ip=client_ip(request))
    db.delete(payment)
    db.flush()
    if contract:
        finance_svc.allocate_plans(db, contract)
    commit_retry(db)
    return redirect(f"/contracts/{payment.contract_id}", "收款记录已删除（已重算应收）")


@router.get("/{payment_id}/edit")
def edit_payment_form(
    request: Request,
    payment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require("payment:edit")),
):
    payment = get_or_404(db, Payment, payment_id, "收款记录")
    contract = db.get(Contract, payment.contract_id)
    users = {u.id: u for u in db.scalars(select(User)).all()}
    photos = photo_svc.photos_by_payment(db, [payment.id]).get(payment.id, [])
    return render(
        request,
        "payments/edit.html",
        payment=payment,
        contract=contract,
        users=users,
        photos=photos,
        methods=METHODS,
    )


@router.post("/{payment_id}/edit")
async def update_payment(
    request: Request,
    payment_id: int,
    amount: str = Form(...),
    paid_at: str = Form(""),
    method: str = Form(""),
    received_by: str = Form(""),
    voucher_no: str = Form(""),
    remark: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(require("payment:edit")),
):
    payment = get_or_404(db, Payment, payment_id, "收款记录")
    contract = db.get(Contract, payment.contract_id)
    if not contract:
        return redirect("/payments", "合同不存在", "err")
    if (frozen := settlement_svc.block_reason(contract, "payment")):
        return redirect(f"/contracts/{contract.id}", frozen, "err")
    amt = parse_float(amount)
    is_refund = amt < 0
    if is_refund and not has_perm(user, "payment:refund"):
        return render(request, "403.html", status_code=403)
    # 校验：从已收合计里先扣除旧金额，再按新金额做超收 / 退款越界校验
    summary = finance_svc.contract_summary(db, contract)
    summary.received = round(summary.received - (payment.amount or 0), 2)
    problem = finance_svc.check_payment(contract, summary, amt, is_refund=is_refund)
    if problem:
        return redirect(f"/payments/{payment.id}/edit", problem, "err")

    old_vals = {
        "amount": payment.amount,
        "paid_at": payment.paid_at,
        "method": payment.method,
        "voucher_no": payment.voucher_no,
        "received_by": payment.received_by,
        "remark": payment.remark,
    }
    payment.amount = amt
    payment.paid_at = paid_at.strip() or today_str()
    payment.method = method.strip() or None
    rid = parse_int(received_by)
    if rid and db.get(User, rid):
        payment.received_by = rid
    payment.voucher_no = voucher_no.strip() or None
    payment.remark = remark.strip() or None

    # 新增付款截图（已有截图的删除走 /photos/{id}/delete）
    saved, dedup, errors = 0, 0, []
    for f in files:
        if not f or not f.filename:
            continue
        raw = await f.read()
        photo, msg = photo_svc.save_photo(
            db,
            customer_id=contract.customer_id,
            kind=PAYMENT_PHOTO_KIND,
            raw=raw,
            orig_name=f.filename,
            uploaded_by=user.id,
            contract_id=contract.id,
            payment_id=payment.id,
        )
        if photo is None:
            errors.append(f"{f.filename}: {msg}")
        else:
            if "重复" in msg:
                dedup += 1
            else:
                saved += 1

    finance_svc.allocate_plans(db, contract)
    log_action(
        db,
        user,
        "update",
        "payments",
        payment.id,
        old=old_vals,
        new=payment,
        ip=client_ip(request),
    )
    commit_retry(db)
    message = "收款已修改"
    if saved or dedup:
        message += f"；付款截图新增 {saved} 张、去重 {dedup} 张"
    if errors:
        message += "；截图失败：" + "；".join(errors)
    return redirect("/payments", message, "err" if errors else "ok")
