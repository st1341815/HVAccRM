"""生成一份演示数据，便于调试：楼盘 + 房号 + 客户（自动 4 工序）+ 合同 + 收款计划。

用法：
    python -m scripts.seed_demo            # 默认只在空库时写入
    python -m scripts.seed_demo --force    # 已有客户也追加
幂等：以楼盘名 / 客户名为键，重复执行不会重复插入。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.audit import log_action  # noqa: E402
from app.bootstrap import ensure_fts, run_migrations, seed_admin, seed_stages  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Contract,
    Customer,
    Payment,
    PaymentPlan,
    Project,
    Room,
    User,
    now_iso,
    today_str,
)
from app.services import tasks as task_svc  # noqa: E402


def get_or_create_project(db, name: str, **kw) -> Project:
    project = db.scalars(select(Project).where(Project.name == name)).first()
    if project:
        return project
    project = Project(name=name, **kw)
    db.add(project)
    db.flush()
    return project


def get_or_create_room(db, project: Project, building: str, unit: str, room_no: str, **kw) -> Room:
    room = db.scalars(
        select(Room)
        .where(Room.project_id == project.id)
        .where(Room.building == building)
        .where(Room.unit == unit)
        .where(Room.room_no == room_no)
    ).first()
    if room:
        return room
    room = Room(project_id=project.id, building=building, unit=unit, room_no=room_no, **kw)
    db.add(room)
    db.flush()
    return room


def main(force: bool = False) -> None:
    get_settings().ensure_dirs()
    run_migrations()
    ensure_fts()
    seed_stages()
    seed_admin()

    with SessionLocal() as db:
        admin = db.scalars(select(User).where(User.role == "admin")).first()
        if db.scalars(select(Customer)).first() and not force:
            print("库中已有客户数据，跳过演示数据写入（如需追加请加 --force）")
            return

        p1 = get_or_create_project(
            db, "中海云麓公馆", city="杭州", district="西湖区", developer="中海地产", delivery_date="2026-06-30", total_units=860
        )
        p2 = get_or_create_project(
            db, "万科天空之城", city="杭州", district="余杭区", developer="万科", delivery_date="2026-09-30", total_units=1200
        )
        rooms = [
            get_or_create_room(db, p1, "3号楼", "1单元", "1203", floor=12, area=118.5, layout="3室2厅"),
            get_or_create_room(db, p1, "5号楼", "2单元", "1203", floor=12, area=121.0, layout="3室2厅"),
            get_or_create_room(db, p1, "7号楼", "1单元", "0805", floor=8, area=95.0, layout="2室2厅"),
            get_or_create_room(db, p2, "A栋", "1单元", "2101", floor=21, area=143.0, layout="4室2厅"),
            get_or_create_room(db, p2, "B栋", "3单元", "1602", floor=16, area=110.0, layout="3室2厅"),
        ]

        demo_customers = [
            {"name": "王建国", "room": rooms[0], "source": "楼盘扫楼", "level": "A", "type": "家装业主", "industry": "橱柜定制",
             "phone": "13800001111", "contract": (36800.0, 0.0, [("定金", 5000, -20), ("首期款", 15000, 0), ("尾款", 16800, 30)]), "paid": [5000.0]},
            {"name": "李慧敏", "room": rooms[1], "source": "设计师推荐", "level": "B", "type": "家装业主", "industry": "衣柜定制",
             "phone": "13900002222", "contract": (24500.0, 500.0, [("定金", 3000, -30), ("尾款", 21000, 15)]), "paid": [3000.0, 5000.0]},
            {"name": "杭州优家装饰工程有限公司", "room": rooms[3], "source": "装修公司", "level": "A", "type": "工程客户", "industry": "门窗工程",
             "phone": "0571-88886666", "contract": (128000.0, 3000.0, [("预付 30%", 38400, -40), ("进度款 40%", 51200, 10), ("尾款 30%", 38400, 60)]), "paid": [38400.0]},
            {"name": "陈志远", "room": rooms[2], "source": "自然到店", "level": "C", "type": "家装业主", "industry": "全屋定制",
             "phone": "13700003333", "contract": None, "paid": []},
            {"name": "赵晓婷", "room": rooms[4], "source": "老客户转介", "level": "B", "type": "家装业主", "industry": "橱柜+衣柜",
             "phone": "13600004444", "contract": (41200.0, 1200.0, [("定金", 6000, -10), ("中期款", 20000, 20), ("尾款", 14000, 55)]), "paid": [6000.0]},
        ]

        from datetime import date, timedelta

        for item in demo_customers:
            if db.scalars(select(Customer).where(Customer.name == item["name"])).first():
                continue
            customer = Customer(
                owner_id=admin.id if admin else 1,
                name=item["name"],
                type=item["type"],
                industry=item["industry"],
                source=item["source"],
                level=item["level"],
                status="active",
                room_id=item["room"].id,
                address=f"{item['room'].project.name} {item['room'].label}",
                notes=f"演示数据：由 scripts/seed_demo.py 生成，联系人电话 {item['phone']}",
                created_by=admin.id if admin else 1,
                created_at=now_iso(),
                updated_at=now_iso(),
            )
            db.add(customer)
            db.flush()
            from app.models import Contact

            db.add(Contact(customer_id=customer.id, name=item["name"], phone=item["phone"], is_primary=1))
            task_svc.generate_tasks(db, customer, assignee_id=customer.owner_id)

            contract_spec = item["contract"]
            if contract_spec:
                total, discount, plans = contract_spec
                contract = Contract(
                    customer_id=customer.id,
                    no=f"HT{today_str().replace('-', '')[:6]}-{customer.id:03d}",
                    sign_date=today_str(),
                    total_amount=total,
                    discount=discount,
                    status="active",
                    notes="演示合同",
                    created_by=admin.id if admin else 1,
                    created_at=now_iso(),
                )
                db.add(contract)
                db.flush()
                for i, (label, amount, offset) in enumerate(plans, start=1):
                    db.add(
                        PaymentPlan(
                            contract_id=contract.id,
                            label=label,
                            amount=amount,
                            due_date=(date.today() + timedelta(days=offset)).isoformat(),
                            sort_order=i,
                            status="pending",
                        )
                    )
                for amount in item["paid"]:
                    db.add(
                        Payment(
                            contract_id=contract.id,
                            amount=amount,
                            paid_at=today_str(),
                            method="微信",
                            received_by=admin.id if admin else 1,
                            remark="演示收款",
                            created_at=now_iso(),
                        )
                    )
                db.flush()
                from app.services import finance as finance_svc

                finance_svc.allocate_plans(db, contract)

        log_action(db, admin, "seed_demo", "system", None, new={"force": force})
        db.commit()
        print("演示数据写入完成：楼盘 2 个、房号 5 个、客户 5 位（含合同与收款）")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
