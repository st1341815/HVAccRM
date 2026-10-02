"""SQLAlchemy ORM 模型 —— 严格对应开发文档第四章 Schema（含少量实现必需的补充列）。"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str() -> str:
    return date.today().isoformat()


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------- 用户与权限
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)  # 姓名（界面优先展示，账号仅用于登录/审计）
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="sales")
    data_scope = Column(String, default="self")
    must_change_password = Column(Integer, default=0)
    session_version = Column(Integer, default=1)
    totp_enabled = Column(Integer, default=0)
    totp_secret = Column(String)
    is_active = Column(Integer, default=1)
    last_login_at = Column(String)
    created_at = Column(String, nullable=False, default=now_iso)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.id} {self.username} {self.role}>"

    @property
    def display_name(self) -> str:
        """界面展示名：优先姓名，未填则回落到登录账号。"""
        return (self.full_name or "").strip() or self.username

    @property
    def name_with_account(self) -> str:
        """姓名（账号）——需要同时看清是谁和用哪个账号登录时使用。"""
        name = (self.full_name or "").strip()
        return f"{name}（{self.username}）" if name and name != self.username else self.username


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    username = Column(String)  # 冗余保存，用户删除后仍可追溯
    action = Column(String, nullable=False)
    table_name = Column(String)
    record_id = Column(Integer)
    old_val = Column(Text)
    new_val = Column(Text)
    ip = Column(String)
    created_at = Column(String, nullable=False, default=now_iso)


class CustomerShare(Base):
    __tablename__ = "customer_shares"

    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    granted_by = Column(Integer, ForeignKey("users.id"))
    granted_at = Column(String, nullable=False, default=now_iso)


# ------------------------------------------------------------ 楼盘与房号
class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    city = Column(String)
    district = Column(String)
    address = Column(String)
    developer = Column(String)
    delivery_date = Column(String)
    total_units = Column(Integer)
    notes = Column(Text)
    created_at = Column(String, default=now_iso)

    rooms = relationship("Room", back_populates="project", cascade="all, delete-orphan")
    buildings = relationship("Building", back_populates="project", cascade="all, delete-orphan")

    @property
    def label(self) -> str:
        """选择器里的展示名：楼盘名称-城市-区县（防止同名楼盘混淆）。"""
        return "-".join(p for p in (self.name, self.city, self.district) if p)


# 单元只允许从固定下拉中选择：1单元 ~ 9单元（空 = 该楼栋无单元概念，如自建/独栋）
UNIT_OPTIONS = [f"{i}单元" for i in range(1, 10)]

# 意向产品：固定白名单（多选），不允许自由输入
PRODUCT_OPTIONS = ["锅炉", "空调", "暖气片", "地暖", "明装", "改造", "水机", "新风", "净水"]

# 客户状态：库内保留英文编码（兼容历史数据），界面统一展示中文
CUSTOMER_STATUS_LABELS = {
    "active": "跟进中",
    "won": "已成交",
    "paused": "暂停跟进",
    "lost": "已流失",
}


# 成本费用项目（固定白名单）：材料成本需关联供应商，施工费用需关联安装师傅
COST_CATEGORIES = ["材料成本", "施工费用", "介绍费", "物流成本", "售后成本", "其他"]
# 需要额外关联对象的费用项目
COST_NEEDS_SUPPLIER = "材料成本"
COST_NEEDS_INSTALLER = "施工费用"


def encode_products(values) -> str | None:
    """多选值 → '|A|B|' 存储（两侧带分隔符，可用 LIKE '%|A|%' 精确匹配，避免子串误命中）。"""
    clean = [v for v in dict.fromkeys(values or []) if v in PRODUCT_OPTIONS]
    return "|" + "|".join(clean) + "|" if clean else None


def decode_products(raw: str | None) -> list[str]:
    return [p for p in (raw or "").split("|") if p]


class Room(Base):
    __tablename__ = "rooms"
    __table_args__ = (UniqueConstraint("project_id", "building", "unit", "room_no", name="uq_room"),)

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    building = Column(String, nullable=False)
    unit = Column(String)
    room_no = Column(String, nullable=False)
    floor = Column(Integer)
    area = Column(Float)
    layout = Column(String)
    delivery_date = Column(String)
    created_at = Column(String, default=now_iso)

    project = relationship("Project", back_populates="rooms")

    @property
    def label(self) -> str:
        return "-".join(p for p in (self.building, self.unit, self.room_no) if p)

    @property
    def building_alnum(self) -> str:
        """楼栋号的字母 + 数字部分（去掉中文），用于房号简称。"""
        return "".join(ch for ch in (self.building or "") if ch.isascii() and ch.isalnum())

    @property
    def short_label(self) -> str:
        """房号简称（不含小区名）：楼栋字母数字#房号。"""
        b = self.building_alnum
        return f"{b}#{self.room_no}" if b else (self.room_no or "")


class Building(Base):
    """楼栋标准化字典：按楼盘预录、去重，房号表单从这里下拉选择，避免自由填写不一致。"""

    __tablename__ = "buildings"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_building"),)

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    name = Column(String, nullable=False)
    created_at = Column(String, default=now_iso)

    project = relationship("Project", back_populates="buildings")


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    type = Column(String)  # 家装 / 工程 / 经销商 …
    phone = Column(String, index=True)  # 客户手机号（客户级，与联系人电话分开）
    wechat = Column(String)
    products = Column(String)  # 意向产品多选，编码为 "|锅炉|地暖|" 便于精确匹配
    industry = Column(String)  # 已不再在表单采集，保留列以兼容 FTS/历史数据
    source = Column(String)  # 渠道来源
    level = Column(String)  # A/B/C
    status = Column(String, default="active")
    room_id = Column(Integer, ForeignKey("rooms.id"))
    decor_stage = Column(String)  # 同上：保留列，表单已移除
    is_showroom = Column(Integer, default=0)
    address = Column(String)
    notes = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(String, nullable=False, default=now_iso)
    updated_at = Column(String, nullable=False, default=now_iso, onupdate=now_iso)

    room = relationship("Room")
    owner = relationship("User", foreign_keys=[owner_id])
    contacts = relationship("Contact", back_populates="customer", cascade="all, delete-orphan")
    contracts = relationship("Contract", back_populates="customer", cascade="all, delete-orphan")
    tasks = relationship("Task", back_populates="customer", cascade="all, delete-orphan")
    photos = relationship("Photo", back_populates="customer", cascade="all, delete-orphan")

    @property
    def room_label(self) -> str:
        if not self.room:
            return ""
        return f"{self.room.project.name if self.room.project else ''} {self.room.label}"

    @property
    def room_label_short(self) -> str:
        """房号简称：小区名 + 楼栋字母数字#房号（去掉中文楼栋名与单元），便于手机查看。"""
        if not self.room:
            return ""
        pname = self.room.project.name if self.room.project else ""
        return f"{pname}{self.room.short_label}"

    @property
    def product_list(self) -> list[str]:
        return decode_products(self.products)

    @property
    def primary_contact(self) -> "Contact | None":
        for c in self.contacts:
            if c.is_primary:
                return c
        return self.contacts[0] if self.contacts else None


class Contact(Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True)
    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    title = Column(String)
    phone = Column(String)
    wechat = Column(String)
    email = Column(String)
    is_primary = Column(Integer, default=0)
    birthday = Column(String)
    created_at = Column(String, nullable=False, default=now_iso)

    customer = relationship("Customer", back_populates="contacts")


# ------------------------------------------------------------ 合同与收款
class Contract(Base):
    __tablename__ = "contracts"

    id = Column(Integer, primary_key=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    no = Column(String, unique=True)
    sign_date = Column(String, nullable=False)
    total_amount = Column(Float, nullable=False, default=0)
    product_type = Column(String)  # 产品类型（单选，PRODUCT_OPTIONS 白名单）
    discount = Column(Float, default=0)  # 已不在表单采集，保留列以兼容历史数据
    status = Column(String, default="active")
    notes = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(String, default=now_iso)

    customer = relationship("Customer", back_populates="contracts")
    items = relationship("ContractItem", back_populates="contract", cascade="all, delete-orphan")
    plans = relationship("PaymentPlan", back_populates="contract", cascade="all, delete-orphan")
    costs = relationship(
        "ContractCost",
        back_populates="contract",
        cascade="all, delete-orphan",
        order_by="ContractCost.id",
    )
    payments = relationship("Payment", back_populates="contract", cascade="all, delete-orphan")
    change_orders = relationship("ChangeOrder", back_populates="contract", cascade="all, delete-orphan")


class ContractItem(Base):
    __tablename__ = "contract_items"

    id = Column(Integer, primary_key=True)
    contract_id = Column(Integer, ForeignKey("contracts.id", ondelete="CASCADE"))
    product_name = Column(String, nullable=False)
    spec = Column(String)
    qty = Column(Float, default=0)
    unit = Column(String)
    unit_price = Column(Float, default=0)
    amount = Column(Float, default=0)
    notes = Column(Text)

    contract = relationship("Contract", back_populates="items")


class PaymentPlan(Base):
    __tablename__ = "payment_plans"

    id = Column(Integer, primary_key=True)
    contract_id = Column(Integer, ForeignKey("contracts.id", ondelete="CASCADE"))
    label = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    due_date = Column(String)
    sort_order = Column(Integer, default=0)
    status = Column(String, default="pending")
    paid_amount = Column(Float, default=0)  # 由收款分摊计算结果写入，便于列表展示
    remark = Column(String)

    contract = relationship("Contract", back_populates="plans")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True)
    contract_id = Column(Integer, ForeignKey("contracts.id", ondelete="CASCADE"))
    amount = Column(Float, nullable=False)
    paid_at = Column(String, nullable=False)
    method = Column(String)
    voucher_no = Column(String)
    received_by = Column(Integer, ForeignKey("users.id"))
    remark = Column(Text)
    created_at = Column(String, default=now_iso)

    contract = relationship("Contract", back_populates="payments")


class Supplier(Base):
    """供应商：材料成本必须关联到一个供应商。"""

    __tablename__ = "suppliers"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    contact = Column(String)  # 联系人
    phone = Column(String)
    notes = Column(Text)
    is_active = Column(Integer, default=1)
    created_at = Column(String, nullable=False, default=now_iso)


class ContractCost(Base):
    """合同成本：以合同（单个项目）为单位归集，用于利润核算。"""

    __tablename__ = "contract_costs"

    id = Column(Integer, primary_key=True)
    contract_id = Column(Integer, ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False)
    category = Column(String, nullable=False)  # COST_CATEGORIES
    amount = Column(Float, nullable=False, default=0)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"))  # 材料成本必填
    installer_id = Column(Integer, ForeignKey("users.id"))  # 施工费用必填
    remark = Column(Text)  # 备注：方便后续查询
    spent_at = Column(String)  # 费用发生日期
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(String, nullable=False, default=now_iso)

    contract = relationship("Contract", back_populates="costs")
    supplier = relationship("Supplier")
    installer = relationship("User", foreign_keys=[installer_id])


class ChangeOrder(Base):
    __tablename__ = "change_orders"

    id = Column(Integer, primary_key=True)
    contract_id = Column(Integer, ForeignKey("contracts.id", ondelete="CASCADE"))
    reason = Column(String)
    amount = Column(Float, default=0)
    approved_at = Column(String)
    approved_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(String, default=now_iso)

    contract = relationship("Contract", back_populates="change_orders")


# ------------------------------------------------------------ 施工管理
class StageTemplate(Base):
    __tablename__ = "stage_templates"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    sort_order = Column(Integer, nullable=False)
    default_days = Column(Integer)
    require_photo = Column(Integer, default=0)
    is_active = Column(Integer, default=1)
    output_doc = Column(String)  # 产出物说明


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (UniqueConstraint("customer_id", "stage", name="uq_task_customer_stage"),)

    id = Column(Integer, primary_key=True)
    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False)
    stage = Column(String, nullable=False)
    sort_order = Column(Integer, nullable=False)
    planned_start = Column(String)
    planned_end = Column(String)
    actual_start = Column(String)
    actual_end = Column(String)
    assignee_id = Column(Integer, ForeignKey("users.id"))
    status = Column(String, default="pending")  # pending/ready/doing/done/skipped
    delay_reason = Column(Text)
    skip_reason = Column(Text)
    notes = Column(Text)
    created_at = Column(String, default=now_iso)
    updated_at = Column(String, default=now_iso, onupdate=now_iso)

    customer = relationship("Customer", back_populates="tasks")
    assignee = relationship("User")


# ------------------------------------------------------------ 照片
class Photo(Base):
    __tablename__ = "photos"

    id = Column(Integer, primary_key=True)
    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False)
    task_id = Column(Integer, ForeignKey("tasks.id"))
    contract_id = Column(Integer, ForeignKey("contracts.id"))  # 纸质合同照片归属
    payment_id = Column(Integer, ForeignKey("payments.id"))  # 收款/退款截图归属
    cost_id = Column(Integer, ForeignKey("contract_costs.id"))  # 成本凭证（发票/收据）归属
    kind = Column(String, nullable=False)
    path = Column(String, nullable=False)  # 相对 data/media 的路径
    thumb_path = Column(String)
    width = Column(Integer)
    height = Column(Integer)
    taken_at = Column(String)
    uploaded_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(String, nullable=False, default=now_iso)
    sha256 = Column(String)
    orig_name = Column(String)
    size_bytes = Column(Integer)

    customer = relationship("Customer", back_populates="photos")


Index("ix_photos_customer", Photo.customer_id)
Index("ix_photos_sha", Photo.sha256)
Index("ix_tasks_assignee", Task.assignee_id)
Index("ix_customers_owner", Customer.owner_id)
Index("ix_payments_contract", Payment.contract_id)


# ------------------------------------------------------------ 全文索引
FTS_DDL = [
    """CREATE VIRTUAL TABLE IF NOT EXISTS customers_fts USING fts5(
           name, industry, notes,
           content='customers', content_rowid='id', tokenize='trigram')""",
    """CREATE TRIGGER IF NOT EXISTS customers_fts_ai AFTER INSERT ON customers BEGIN
           INSERT INTO customers_fts(rowid, name, industry, notes)
           VALUES (new.id, new.name, new.industry, new.notes); END""",
    """CREATE TRIGGER IF NOT EXISTS customers_fts_ad AFTER DELETE ON customers BEGIN
           INSERT INTO customers_fts(customers_fts, rowid, name, industry, notes)
           VALUES ('delete', old.id, old.name, old.industry, old.notes); END""",
    """CREATE TRIGGER IF NOT EXISTS customers_fts_au AFTER UPDATE ON customers BEGIN
           INSERT INTO customers_fts(customers_fts, rowid, name, industry, notes)
           VALUES ('delete', old.id, old.name, old.industry, old.notes);
           INSERT INTO customers_fts(rowid, name, industry, notes)
           VALUES (new.id, new.name, new.industry, new.notes); END""",
]

# 标准 9 个施工工序（文档 6.5）
DEFAULT_STAGES = [
    ("上门勘测", 1, 1, 1, "勘测记录 + 现场照"),
    ("前期施工", 2, 3, 1, "施工照"),
    ("后期施工", 3, 3, 1, "施工照"),
    ("调试验收", 4, 1, 1, "验收单 + 完工照"),
]