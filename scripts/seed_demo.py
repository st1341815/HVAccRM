"""演示数据：楼盘 / 房号 / 客户 / 合同 / 收款（带截图）/ 成本（供应商+安装师傅）/ 工序照片。

用法（在容器内执行）：
    python -m scripts.seed_demo            # 空库或已存在演示数据时写入（幂等，可重复执行）
    python -m scripts.seed_demo --force    # 忽略「库中已有客户」的检查，强制补写
    python -m scripts.seed_demo --purge    # 删除全部演示数据（只删本脚本创建的那批）

演示实体统一带「演示」标记，便于识别与一键清除；清理由 --purge 完成，
删除顺序：照片 → 客户（级联合同/收款/成本/工序） → 楼盘 → 供应商 → 演示安装工。
"""
from __future__ import annotations

import io
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.bootstrap import ensure_fts, run_migrations, seed_admin, seed_stages  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Contract,
    ContractCost,
    Customer,
    Payment,
    PaymentPlan,
    Photo,
    Project,
    Room,
    Supplier,
    User,
    now_iso,
    today_str,
)
from app.security import hash_password  # noqa: E402
from app.services import photos as photo_svc  # noqa: E402
from app.services import tasks as task_svc  # noqa: E402

MARK = "演示"
DEMO_PROJECTS = [
    {"name": f"中海云麓公馆（{MARK}）", "city": "合肥", "district": "包河区", "developer": "中海地产", "delivery_date": "2026-06-30", "total_units": 860},
    {"name": f"万科森林公园（{MARK}）", "city": "合肥", "district": "庐阳区", "developer": "万科", "delivery_date": "2026-09-30", "total_units": 1200},
]
DEMO_SUPPLIERS = [
    {"name": f"华东管材（{MARK}）", "contact": "王经理", "phone": "13900001111", "notes": "地暖管/分集水器，月结 30 天"},
    {"name": f"博世锅炉安徽总代（{MARK}）", "contact": "李经理", "phone": "13800002222", "notes": "壁挂炉整机，含厂保"},
    {"name": f"合肥城配物流（{MARK}）", "contact": "调度老周", "phone": "13600003333", "notes": "大件配送，按趟结算"},
]
DEMO_INSTALLER = {"username": f"demo_worker_{MARK}", "full_name": f"张师傅（{MARK}）", "phone": "13500004444"}
DEMO_CUSTOMERS = [
    {
        "name": f"王建国（{MARK}）", "project": 0, "building": "3号楼", "unit": "1单元", "room_no": "1203", "floor": 12, "area": 118.5,
        "phone": "13811110001", "wechat": "wangjg_1980", "type": "家装业主", "source": "楼盘扫楼", "level": "A",
        "products": ["地暖", "新风"],
        "contract": {"amount": 36800.0, "product_type": "地暖", "notes": "全屋地暖 + 新风主机，含安装调试",
                     "plans": [("定金", 5000, -20), ("首期款", 15000, 0), ("尾款", 16800, 30)],
                     "paid": [(5000.0, "微信", -15), (15000.0, "银行转账", 0)],
                     "costs": [("材料成本", 12000.0, "supplier:0", "地暖管材 + 分集水器 + 保温板"),
                               ("施工费用", 4500.0, "installer", "地暖盘管铺装，2 人 3 天"),
                               ("介绍费", 800.0, None, "设计师引荐，按合同额 2%")],
                     "paper_photos": 2},
        "task_done": (1, "现场勘测完成，层高 2.75m，分水器位置定在厨房阳台"),
        "task_doing": 2,
        "task_delay": None,
    },
    {
        "name": f"李慧敏（{MARK}）", "project": 0, "building": "5号楼", "unit": "2单元", "room_no": "1203", "floor": 12, "area": 121.0,
        "phone": "13911110002", "wechat": "lihm_home", "type": "家装业主", "source": "设计师推荐", "level": "B",
        "products": ["空调"],
        "contract": {"amount": 24500.0, "product_type": "空调", "notes": "风管机一拖三",
                     "plans": [("定金", 3000, -30), ("尾款", 21500, 15)],
                     "paid": [(3000.0, "支付宝", -25)],
                     "costs": [("材料成本", 9800.0, "supplier:1", "风管机 3 台 + 铜管"),
                               ("施工费用", 3200.0, "installer", "吊装 + 试压，2 人 2 天"),
                               ("物流成本", 400.0, "supplier:2", "大件配送到户")],
                     "paper_photos": 1},
        "task_done": (1, "已上门勘测，确认吊顶方案"),
        "task_doing": None,
        "task_delay": 2,
    },
    {
        "name": f"杭州优家装饰工程有限公司（{MARK}）", "project": 1, "building": "A栋", "unit": "1单元", "room_no": "2101", "floor": 21, "area": 143.0,
        "phone": "0571-88886666", "wechat": None, "type": "工程客户", "source": "装修公司", "level": "A",
        "products": ["暖气片", "改造"],
        "contract": {"amount": 128000.0, "product_type": "暖气片", "notes": "整层暖气片改造，含旧暖气拆除",
                     "plans": [("预付款 30%", 38400, -40), ("进度款 40%", 51200, 10), ("尾款 30%", 38400, 60)],
                     "paid": [(38400.0, "对公转账", -35)],
                     "costs": [("材料成本", 62000.0, "supplier:1", "钢板暖气片 18 组 + 阀门管件"),
                               ("施工费用", 18000.0, "installer", "拆除旧暖气 + 新装，4 人 6 天"),
                               ("售后成本", 1200.0, None, "预留质保上门费")],
                     "paper_photos": 1},
        "task_done": None, "task_doing": None, "task_delay": None,
    },
    {
        "name": f"陈志远（{MARK}）", "project": 1, "building": "B栋", "unit": "3单元", "room_no": "1602", "floor": 16, "area": 110.0,
        "phone": "13711110003", "wechat": "chenzy_001", "type": "家装业主", "source": "自然到店", "level": "C",
        "products": ["净水", "水机"],
        "contract": None,
        "task_done": None, "task_doing": None, "task_delay": None,
    },
    {
        "name": f"赵晓婷（{MARK}）", "project": 0, "building": "7号楼", "unit": "1单元", "room_no": "0805", "floor": 8, "area": 95.0,
        "phone": "13611110004", "wechat": "zhaoxt2019", "type": "家装业主", "source": "老客户转介", "level": "B",
        "products": ["明装"],
        "contract": {"amount": 41200.0, "product_type": "明装", "notes": "老房明装暖气，走线尽量隐蔽",
                     "plans": [("定金", 6000, -10), ("中期款", 20000, 20), ("尾款", 15200, 55)],
                     "paid": [(6000.0, "现金", -8)],
                     "costs": [("材料成本", 15600.0, "supplier:1", "明装暖气片 6 组 + 铝塑管"),
                               ("施工费用", 5200.0, "installer", "明装走管，2 人 2 天")],
                     "paper_photos": 1},
        "task_done": None, "task_doing": None, "task_delay": None,
    },
]

_IMG_COLORS = {
    "现场": (60, 110, 170),
    "施工": (170, 120, 60),
    "验收": (60, 150, 110),
    "纸质合同": (245, 243, 235),
    "收款截图": (200, 230, 200),
    "成本凭证": (230, 220, 200),
}


def _make_image(kind: str, label: str, seed: int = 0) -> bytes:
    """生成一张带文字的占位图（演示用，不依赖外部字体）。"""
    base = _IMG_COLORS.get(kind, (180, 180, 180))
    color = tuple(min(255, max(0, c + seed * 7 % 40 - 20)) for c in base)
    img = Image.new("RGB", (1200, 800), color)
    draw = ImageDraw.Draw(img)
    draw.rectangle([24, 24, 1176, 776], outline=(255, 255, 255), width=3)
    draw.text((60, 70), f"{kind} · {label}", fill=(255, 255, 255) if sum(color) < 600 else (40, 40, 40))
    draw.text((60, 120), f"oneCRM demo image · {label}", fill=(255, 255, 255) if sum(color) < 600 else (90, 90, 90))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def _get_or_create_project(db, data: dict) -> Project:
    p = db.scalars(select(Project).where(Project.name == data["name"])).first()
    if p:
        return p
    p = Project(**data)
    db.add(p)
    db.flush()
    return p


def _get_or_create_supplier(db, data: dict) -> Supplier:
    s = db.scalars(select(Supplier).where(Supplier.name == data["name"])).first()
    if s:
        return s
    s = Supplier(is_active=1, created_at=now_iso(), **data)
    db.add(s)
    db.flush()
    return s


def _get_or_create_room(db, project: Project, item: dict) -> Room:
    room = db.scalars(
        select(Room)
        .where(Room.project_id == project.id)
        .where(Room.building == item["building"])
        .where(Room.unit == item["unit"])
        .where(Room.room_no == item["room_no"])
    ).first()
    if room:
        return room
    room = Room(
        project_id=project.id,
        building=item["building"],
        unit=item["unit"],
        room_no=item["room_no"],
        floor=item["floor"],
        area=item["area"],
    )
    db.add(room)
    db.flush()
    return room


def purge() -> None:
    with SessionLocal() as db:
        n = {"photos": 0, "customers": 0, "projects": 0, "suppliers": 0, "users": 0, "costs": 0, "contracts": 0}
        for data in DEMO_CUSTOMERS:
            c = db.scalars(select(Customer).where(Customer.name == data["name"])).first()
            if not c:
                continue
            n["photos"] += photo_svc.delete_photos_for(db, customer_id=c.id)
            n["costs"] += db.query(ContractCost).filter(ContractCost.contract_id.in_([x.id for x in c.contracts])).count() if c.contracts else 0
            n["contracts"] += len(c.contracts)
            db.delete(c)
            n["customers"] += 1
        db.flush()
        for data in DEMO_PROJECTS:
            p = db.scalars(select(Project).where(Project.name == data["name"])).first()
            if p:
                db.delete(p)  # 房号级联删除
                n["projects"] += 1
        db.flush()
        for data in DEMO_SUPPLIERS:
            s = db.scalars(select(Supplier).where(Supplier.name == data["name"])).first()
            if s:
                db.delete(s)
                n["suppliers"] += 1
        u = db.scalars(select(User).where(User.username == DEMO_INSTALLER["username"])).first()
        if u:
            db.delete(u)
            n["users"] += 1
        db.commit()
        print(f"已清除演示数据：客户 {n['customers']} 位（合同 {n['contracts']} 份 / 成本 {n['costs']} 条 / 照片 {n['photos']} 张）、"
              f"楼盘 {n['projects']} 个、供应商 {n['suppliers']} 家、演示安装工 {n['users']} 个")


def main(force: bool = False) -> None:
    get_settings().ensure_dirs()
    run_migrations()
    ensure_fts()
    seed_stages()
    seed_admin()
    today = date.today()

    with SessionLocal() as db:
        admin = db.scalars(select(User).where(User.role == "admin")).first()
        owner_id = admin.id if admin else 1
        already = db.scalars(select(Customer).where(Customer.name == DEMO_CUSTOMERS[0]["name"])).first()
        if db.scalars(select(Customer)).first() and not force and not already:
            print("库中已有客户数据，跳过演示数据写入（如需追加请加 --force）")
            return
        if already:
            print("演示数据已存在，跳过（重复执行安全；如需重建请先 --purge）")
            return

        # 安装工账号（施工费用要关联到它）
        installer = db.scalars(select(User).where(User.username == DEMO_INSTALLER["username"])).first()
        if not installer:
            installer = User(
                username=DEMO_INSTALLER["username"],
                full_name=DEMO_INSTALLER["full_name"],
                password_hash=hash_password("Demo123456"),
                role="installer",
                data_scope="self",
                must_change_password=0,
                session_version=1,
                is_active=1,
                created_at=now_iso(),
            )
            db.add(installer)
            db.flush()

        projects = [_get_or_create_project(db, p) for p in DEMO_PROJECTS]
        suppliers = [_get_or_create_supplier(db, s) for s in DEMO_SUPPLIERS]

        stats = {"customers": 0, "contracts": 0, "payments": 0, "costs": 0, "photos": 0, "tasks_touched": 0}
        for idx, item in enumerate(DEMO_CUSTOMERS):
            room = _get_or_create_room(db, projects[item["project"]], item)
            customer = Customer(
                owner_id=owner_id,
                name=item["name"],
                type=item["type"],
                phone=item["phone"],
                wechat=item["wechat"],
                products="|" + "|".join(item["products"]) + "|",
                source=item["source"],
                level=item["level"],
                status="active",
                room_id=room.id,
                notes=f"演示数据：由 scripts/seed_demo.py 生成（可用 --purge 清除）",
                created_by=owner_id,
                created_at=now_iso(),
                updated_at=now_iso(),
            )
            db.add(customer)
            db.flush()
            stats["customers"] += 1

            # 工序任务（按当前模板自动生成，责任人先给演示安装工）
            rows = task_svc.generate_tasks(db, customer, assignee_id=installer.id)
            tasks = sorted(rows if rows else customer.tasks, key=lambda t: t.sort_order or 0)
            db.flush()

            done = item.get("task_done")
            if done and len(tasks) >= done[0]:
                t = tasks[done[0] - 1]
                t.status = "done"
                t.actual_start = (today - timedelta(days=6)).isoformat()
                t.actual_end = (today - timedelta(days=5)).isoformat()
                t.assignee_id = installer.id
                t.notes = f"[{t.actual_end} 完工] {done[1]}"
                stats["tasks_touched"] += 1
                for i in range(2):
                    raw = _make_image("现场", f"{customer.name} {t.stage} #{i + 1}", seed=idx + i)
                    photo, _ = photo_svc.save_photo(
                        db, customer_id=customer.id, kind="现场", raw=raw,
                        orig_name=f"site_{customer.id}_{i + 1}.jpg", uploaded_by=owner_id,
                        task_id=t.id, contract_id=None,
                    )
                    if photo:
                        stats["photos"] += 1

            doing = item.get("task_doing")
            if doing and len(tasks) >= doing:
                t = tasks[doing - 1]
                t.status = "doing"
                t.actual_start = (today - timedelta(days=1)).isoformat()
                t.assignee_id = installer.id
                stats["tasks_touched"] += 1

            delay = item.get("task_delay")
            if delay and len(tasks) >= delay:
                t = tasks[delay - 1]
                t.status = "ready"
                t.planned_start = (today - timedelta(days=9)).isoformat()
                t.planned_end = (today - timedelta(days=4)).isoformat()
                t.assignee_id = installer.id
                stats["tasks_touched"] += 1

            contract_data = item.get("contract")
            if not contract_data:
                continue
            from app.services import numbering as numbering_svc

            contract = Contract(
                customer_id=customer.id,
                no=numbering_svc.reserve_contract_no(db),
                sign_date=(today - timedelta(days=25)).isoformat(),
                total_amount=contract_data["amount"],
                product_type=contract_data["product_type"],
                discount=0,
                status="active",
                notes=contract_data["notes"],
                created_by=owner_id,
                created_at=now_iso(),
            )
            db.add(contract)
            db.flush()
            stats["contracts"] += 1

            for i, (label, amount, day_off) in enumerate(contract_data["plans"], start=1):
                db.add(PaymentPlan(contract_id=contract.id, label=label, amount=amount,
                                   due_date=(today + timedelta(days=day_off)).isoformat(), sort_order=i, status="pending"))
            for i, (amount, method, day_off) in enumerate(contract_data["paid"], start=1):
                payment = Payment(contract_id=contract.id, amount=amount,
                                  paid_at=(today + timedelta(days=day_off)).isoformat(), method=method,
                                  voucher_no=f"DEMO{contract.id:03d}{i:02d}", received_by=owner_id,
                                  remark="演示收款", created_at=now_iso())
                db.add(payment)
                db.flush()
                stats["payments"] += 1
                raw = _make_image("收款截图", f"{customer.name} {amount:,.0f} {method}", seed=idx + i)
                photo, _ = photo_svc.save_photo(
                    db, customer_id=customer.id, kind="收款截图", raw=raw,
                    orig_name=f"pay_{payment.id}.jpg", uploaded_by=owner_id,
                    contract_id=contract.id, payment_id=payment.id,
                )
                if photo:
                    stats["photos"] += 1

            for i in range(int(contract_data.get("paper_photos") or 0)):
                raw = _make_image("纸质合同", f"{contract.no} 第 {i + 1} 页", seed=idx + i)
                photo, _ = photo_svc.save_photo(
                    db, customer_id=customer.id, kind="纸质合同", raw=raw,
                    orig_name=f"contract_{contract.id}_{i + 1}.jpg", uploaded_by=owner_id,
                    contract_id=contract.id,
                )
                if photo:
                    stats["photos"] += 1

            for cat, amount, link, remark in contract_data["costs"]:
                sid = suppliers[int(link.split(":")[1])].id if link and link.startswith("supplier:") else None
                iid = installer.id if link == "installer" else None
                cost = ContractCost(
                    contract_id=contract.id, category=cat, amount=amount,
                    supplier_id=sid, installer_id=iid, remark=remark,
                    spent_at=(today - timedelta(days=12)).isoformat(), created_by=owner_id, created_at=now_iso(),
                )
                db.add(cost)
                db.flush()
                stats["costs"] += 1
                raw = _make_image("成本凭证", f"{cat} {amount:,.0f} {remark[:12]}", seed=idx + len(cat))
                photo, _ = photo_svc.save_photo(
                    db, customer_id=customer.id, kind="成本凭证", raw=raw,
                    orig_name=f"cost_{cost.id}.jpg", uploaded_by=owner_id,
                    contract_id=contract.id, cost_id=cost.id,
                )
                if photo:
                    stats["photos"] += 1

        db.commit()
        print("演示数据写入完成：" + "，".join(f"{k} {v}" for k, v in stats.items()))
        print(f"演示安装工：{DEMO_INSTALLER['full_name']}（账号 {DEMO_INSTALLER['username']}，密码 Demo123456，角色 installer）")
        print("清除演示数据：python -m scripts.seed_demo --purge")


if __name__ == "__main__":
    if "--purge" in sys.argv:
        purge()
    else:
        main(force="--force" in sys.argv)
