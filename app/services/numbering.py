"""单据编号生成：合同号 = ONE + 8 位年月日 + 4 位顺数（顺数按年重置）。"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Contract

CONTRACT_PREFIX = "ONE"
SEQ_WIDTH = 4


def _year_seq(no: str | None, year: int) -> int:
    """从既有编号里取该年度的顺数（尾部 4 位数字）。"""
    if not no or not no.startswith(f"{CONTRACT_PREFIX}{year}"):
        return 0
    tail = no[-SEQ_WIDTH:]
    return int(tail) if tail.isdigit() else 0


def next_contract_no(db: Session, when: date | None = None) -> str:
    """生成下一个合同号。顺数只统计同年度编号，因此每年 1 月 1 日自动从 0001 重新开始。"""
    d = when or date.today()
    year = d.year
    rows = db.scalars(select(Contract.no).where(Contract.no.like(f"{CONTRACT_PREFIX}{year}%"))).all()
    seq = max((_year_seq(no, year) for no in rows), default=0) + 1
    return f"{CONTRACT_PREFIX}{d.strftime('%Y%m%d')}{seq:0{SEQ_WIDTH}d}"


def reserve_contract_no(db: Session, when: date | None = None, tries: int = 20) -> str:
    """生成未被占用的合同号（并发/历史数据冲突时向后顺延）。"""
    d = when or date.today()
    no = next_contract_no(db, d)
    for _ in range(tries):
        if not db.scalars(select(Contract).where(Contract.no == no)).first():
            return no
        seq = int(no[-SEQ_WIDTH:]) + 1
        no = f"{no[:-SEQ_WIDTH]}{seq:0{SEQ_WIDTH}d}"
    return no
