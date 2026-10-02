"""全文检索：FTS5(trigram) 优先，短查询退化为 LIKE（中文 1-2 字无法触发 trigram）。"""
from __future__ import annotations

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from ..models import Customer, Contact, Project, Room


def _like_conditions(q: str):
    like = f"%{q}%"
    contact_ids = select(Contact.customer_id).where(
        or_(Contact.name.like(like), Contact.phone.like(like), Contact.wechat.like(like))
    )
    return or_(
        Customer.name.like(like),
        Customer.phone.like(like),
        Customer.wechat.like(like),
        Customer.industry.like(like),
        Customer.notes.like(like),
        Customer.address.like(like),
        Customer.id.in_(contact_ids),
    )


def search_customers(db: Session, q: str, scope_cond=None, limit: int = 200) -> list[Customer]:
    q = (q or "").strip()
    stmt = select(Customer)
    if q:
        cond = _like_conditions(q)
        # FTS5 trigram 需要 ≥3 字符；手机号（11 位）能命中 FTS 但手机号未进索引，
        # 因此 LIKE 条件始终并集进结果，避免「搜手机号搜不到客户」。
        if len(q) >= 3:
            try:
                rows = db.execute(
                    text(
                        "SELECT rowid FROM customers_fts WHERE customers_fts MATCH :m LIMIT :lim"
                    ),
                    {"m": f'"{q}"', "lim": limit},
                ).all()
                ids = [r[0] for r in rows]
                if ids:
                    cond = or_(Customer.id.in_(ids), cond)
            except Exception:
                pass
        stmt = stmt.where(cond)
    if scope_cond is not None:
        stmt = stmt.where(scope_cond)
    stmt = stmt.order_by(Customer.updated_at.desc()).limit(limit)
    return list(db.scalars(stmt).all())


def customer_ids_by_keyword(db: Session, q: str) -> list[int]:
    """按关键字匹配客户 ID：姓名 / 手机号 / 房号（楼栋·单元·房号）/ 楼盘名。

    供「合同台账 / 收款流水 / 利润核算」等列表筛选使用，替代原来的客户 ID 精确过滤。
    """
    q = (q or "").strip()
    if not q:
        return []
    kw = f"%{q}%"
    return list(
        db.scalars(
            select(Customer.id)
            .outerjoin(Room, Customer.room_id == Room.id)
            .outerjoin(Project, Room.project_id == Project.id)
            .where(
                or_(
                    Customer.name.like(kw),
                    Customer.phone.like(kw),
                    Room.building.like(kw),
                    Room.unit.like(kw),
                    Room.room_no.like(kw),
                    Project.name.like(kw),
                )
            )
        ).all()
    )


def fts_status(db: Session) -> dict:
    try:
        n = db.execute(text("SELECT count(*) FROM customers_fts")).scalar_one()
        return {"ok": True, "indexed": int(n)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
