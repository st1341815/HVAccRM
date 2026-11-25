"""电子合同模板管理：后台增删改 + 上传导入 + 公司抬头 + 打印时选模板。

**为什么要管这一层**（v1.14 需求）：合同正文以前写死在代码里，客户要加一条
免责声明、换成自家格式，就得改代码发版。现在把正文搬进库、后台可编辑，
代价是「谁都能改坏」——所以这里钉住几条底线：

1. 空库首次进后台会自动落地一份内置范本（开箱就能打印，不用先写模板）；
2. 任何时候**至少留一份**模板，删到只剩一份时拒绝（否则电子合同没正文）；
3. 默认模板全局唯一：`set_default` 会把别的一律置 0；
4. 抬头字段（乙方名称 / 开户行 / 账号）走 settings，保存后可读回；
5. 后台每个写操作都带 csrf_token（模板级断言，防止以后漏一个）。
"""
from __future__ import annotations

import asyncio
import io
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import select

from app.models import ContractTemplate
from app.services import contract_docs as doc_svc

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "app" / "templates" / "admin" / "contract_templates.html"
ADMIN_ROUTER = ROOT / "app" / "routers" / "admin.py"


# ------------------------------------------------------------------ 内置范本
def test_empty_db_seeds_builtin_template(db):
    assert db.scalars(select(ContractTemplate)).all() == []
    row = doc_svc.ensure_default_template(db, created_by=None)
    db.flush()
    assert row.is_default == 1
    assert row.is_active == 1
    assert "{{甲方名称}}" in row.content, "内置范本必须带占位符，否则打印出来是死文本"
    assert "{{明细表}}" in row.content, "合同必须有明细表位置"


def test_ensure_default_is_idempotent(db):
    first = doc_svc.ensure_default_template(db)
    db.flush()
    again = doc_svc.ensure_default_template(db)
    assert again.id == first.id
    assert len(db.scalars(select(ContractTemplate)).all()) == 1


# ------------------------------------------------------------------ 默认与选用
def test_set_default_is_exclusive(db):
    a = doc_svc.ensure_default_template(db)
    b = ContractTemplate(name="简版", content="<p>{{客户名称}}</p>", is_default=0, is_active=1)
    db.add(b)
    db.flush()

    doc_svc.set_default(db, b.id)
    db.flush()
    db.refresh(a)
    db.refresh(b)
    assert (b.is_default, a.is_default) == (1, 0), "默认模板必须全局唯一"


def test_pick_template_falls_back_to_default(db):
    d = doc_svc.ensure_default_template(db)
    off = ContractTemplate(name="停用的", content="x", is_default=0, is_active=0)
    db.add(off)
    db.flush()

    assert doc_svc.pick_template(db, None).id == d.id
    assert doc_svc.pick_template(db, off.id).id == d.id, "指定了停用模板应回落默认"
    assert doc_svc.pick_template(db, 99999).id == d.id, "不存在的 id 也要回落，不能抛"


def test_list_puts_default_first(db):
    first = doc_svc.ensure_default_template(db)
    other = ContractTemplate(name="别的", content="x", is_default=0, is_active=1)
    db.add(other)
    db.flush()
    assert doc_svc.list_templates(db)[0].id == first.id


# ------------------------------------------------------------------ 公司抬头
def test_company_info_roundtrip(db):
    doc_svc.save_company_info(db, {"doc.company_name": " 某某暖通 ", "doc.company_bank": "工商银行"})
    db.flush()
    info = doc_svc.company_info(db)
    assert info["doc.company_name"] == "某某暖通"  # 去空白
    assert info["doc.company_bank"] == "工商银行"
    assert doc_svc.company_info(db)["doc.company_account"] == "", "没填的抬头留空，不要编默认值"


# ------------------------------------------------------------------ 模板约定
def test_template_page_has_management_entries():
    html = PAGE.read_text()
    for action in (
        "/admin/contract-templates/new",
        "/contract-templates/company",
        "/contract-templates/upload",
        "/default",
        "/delete",
    ):
        assert action in html, f"后台模板页缺少入口：{action}"


def test_every_post_form_carries_csrf():
    html = PAGE.read_text()
    posts = [chunk for chunk in html.split("<form") if 'method="post"' in chunk.split(">")[0]]
    assert posts, "页面上应至少有一个 POST 表单"
    for chunk in posts:
        head = chunk.split(">")[0]
        assert "csrf_token" in chunk[: chunk.index("</form>")], f"表单缺 csrf_token：{head}"


def test_upload_rejects_non_text_formats():
    """上传只收文本格式——PDF/Word 转出来的正文是废的，宁可明确拒绝。"""
    src = ADMIN_ROUTER.read_text()
    snippet = src[src.index("contract_template_upload") :]
    assert '(".html", ".htm", ".txt", ".md", ".markdown")' in snippet
    assert "512 * 1024" in snippet, "上传大小要设上限"


def test_admin_router_registers_all_template_routes():
    src = ADMIN_ROUTER.read_text()
    for path in (
        '@router.get("/contract-templates")',
        '@router.post("/contract-templates/new")',
        '@router.post("/contract-templates/{template_id}/update")',
        '@router.post("/contract-templates/{template_id}/delete")',
        '@router.post("/contract-templates/{template_id}/default")',
        '@router.post("/contract-templates/upload")',
        '@router.post("/contract-templates/company")',
    ):
        assert path in src, f"缺少路由：{path}"


# ---------------------------------------------------------------- 路由真跑一遍
# 只测 service 抓不到 `log_action(detail=...)` 这类运行期炸点，所以直调路由函数。
from starlette.requests import Request  # noqa: E402

from app.routers import admin as admin_router  # noqa: E402


def _req(path: str = "/admin/contract-templates", method: str = "POST") -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
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


def test_page_renders_with_builtin_template(db, admin):
    """第一次进来，库里一份模板都没有 → 自动落地内置范本，页面能渲染出来。"""
    from sqlalchemy import select

    assert db.scalars(select(ContractTemplate)).all() == []
    resp = admin_router.contract_templates_page(request=_req(path="/admin/contract-templates", method="GET"), db=db, user=admin)
    assert resp.status_code == 200
    rows = db.scalars(select(ContractTemplate)).all()
    assert len(rows) == 1 and rows[0].is_default == 1
    body = resp.body.decode()
    assert "合同模板" in body and "标准工程合同（内置范本）" in body


def test_create_update_default_delete_roundtrip(db, admin):
    tpl = doc_svc.ensure_default_template(db, admin.id)

    assert admin_router.contract_template_new(
        request=_req(), name="简式报价单", content="甲方 {{客户名称}}", db=db, user=admin, _csrf=None
    ).status_code == 303
    new_row = db.scalars(select(ContractTemplate).where(ContractTemplate.name == "简式报价单")).first()
    assert new_row is not None and new_row.is_default == 0

    assert admin_router.contract_template_update(
        request=_req(), template_id=new_row.id, name="简式报价单 V2", content="改成 {{合同号}}",
        is_active="1", db=db, user=admin, _csrf=None,
    ).status_code == 303
    db.refresh(new_row)
    assert new_row.name == "简式报价单 V2" and new_row.content == "改成 {{合同号}}"

    assert admin_router.contract_template_default(
        request=_req(), template_id=new_row.id, db=db, user=admin, _csrf=None
    ).status_code == 303
    db.refresh(new_row)
    db.refresh(tpl)
    assert new_row.is_default == 1 and tpl.is_default == 0
    assert doc_svc.default_template(db).id == new_row.id

    assert admin_router.contract_template_delete(
        request=_req(), template_id=tpl.id, db=db, user=admin, _csrf=None
    ).status_code == 303
    assert db.get(ContractTemplate, tpl.id) is None


def test_cannot_delete_last_template(db, admin):
    """删空会让电子合同没法打印 —— 保留至少一份。"""
    only = doc_svc.ensure_default_template(db, admin.id)
    resp = admin_router.contract_template_delete(
        request=_req(), template_id=only.id, db=db, user=admin, _csrf=None
    )
    assert resp.status_code == 303
    assert db.get(ContractTemplate, only.id) is not None


def test_duplicate_name_rejected(db, admin):
    doc_svc.ensure_default_template(db, admin.id)
    resp = admin_router.contract_template_new(
        request=_req(), name="标准工程合同（内置范本）", content="x", db=db, user=admin, _csrf=None
    )
    assert resp.status_code == 303
    assert len(db.scalars(select(ContractTemplate)).all()) == 1


def test_upload_html_imports_template(db, admin):
    """上传 .html 导入 → 名字撞车自动加序号。"""
    doc_svc.ensure_default_template(db, admin.id)
    up = UploadFile(file=io.BytesIO("<p>甲方 {{甲方名称}}</p>".encode()), filename="工程合同.html")
    resp = asyncio.run(
        admin_router.contract_template_upload(request=_req(), file=up, name="", db=db, user=admin, _csrf=None)
    )
    assert resp.status_code == 303
    row = db.scalars(select(ContractTemplate).where(ContractTemplate.name == "工程合同")).first()
    assert row is not None and "{{甲方名称}}" in row.content

    up2 = UploadFile(file=io.BytesIO(b"<p>dup</p>"), filename="工程合同.html")
    asyncio.run(
        admin_router.contract_template_upload(request=_req(), file=up2, name="", db=db, user=admin, _csrf=None)
    )
    names = [r.name for r in db.scalars(select(ContractTemplate).order_by(ContractTemplate.id)).all()]
    assert names.count("工程合同") == 1 and "工程合同 2" in names


def test_upload_rejects_pdf(db, admin):
    doc_svc.ensure_default_template(db, admin.id)
    up = UploadFile(file=io.BytesIO(b"%PDF-1.4"), filename="合同.pdf")
    resp = asyncio.run(
        admin_router.contract_template_upload(request=_req(), file=up, name="", db=db, user=admin, _csrf=None)
    )
    assert resp.status_code == 303
    assert len(db.scalars(select(ContractTemplate)).all()) == 1


def test_company_info_saved_and_used_in_render(db, admin):
    """抬头存进 settings，合同正文里的 {{乙方名称}} 才填得上。"""
    from app.models import Contract, Customer

    c = Customer(name="张三", phone="13800000000", type="家装", owner_id=admin.id)
    db.add(c)
    db.flush()
    ctr = Contract(customer_id=c.id, no="HT-001", product_type="中央空调", total_amount=10000, sign_date="2026-05-01")
    db.add(ctr)
    db.flush()

    form = FakeForm({"doc.company_name": "苏州某某暖通工程有限公司", "doc.company_phone": "0512-1234567"})
    resp = asyncio.run(
        admin_router.contract_company_save(request=_req_with_form(form), db=db, user=admin, _csrf=None)
    )
    assert resp.status_code == 303
    info = doc_svc.company_info(db)
    assert info["doc.company_name"] == "苏州某某暖通工程有限公司"

    body, missing = doc_svc.render_contract_body(db, ctr)
    assert "苏州某某暖通工程有限公司" in body
    assert "0512-1234567" in body


class FakeForm(dict):
    """够 `await request.form()` 用的最小替身。"""

    def get(self, key, default=None):  # noqa: D102
        return dict.get(self, key, default)


def _req_with_form(form: FakeForm) -> Request:
    req = _req(path="/admin/contract-templates/company")

    async def _form():
        return form

    req.form = _form  # type: ignore[assignment]
    return req
