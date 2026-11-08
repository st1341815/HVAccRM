"""灯箱翻页分组（`data-photo-group`）的守卫测试。

**背景（v1.12.2 修复的 bug）**：在「导航 → 成本 → 成本明细」点开某笔记录的
凭证缩略图，灯箱里按「下一张」会翻到**别的记录**的凭证。根因是
`app/templates/layout.html` 的 `collect(a)` 在没有 `.gallery` / `.task-photos` /
`.case-thumbs` 容器时，兜底成了「整页所有 `a.photo-link`」——表格单元格里的
凭证缩略图正好落进这个兜底。

**修法有两半，缺一不可**：
  A. 模板：表格行内放凭证的 `<td>` 标上 `data-photo-group`；
  B. `collect()` 先认 `[data-photo-group]`，只在本组内翻页，整页遍历降为最后一级兜底。

**v1.12.3 起，列表侧与施工看板统一**：`<td class="photo-cell" data-photo-group>`
只渲染前 3 张（`phs[:3]`），第 4 张起写成**无 img 的隐藏锚点**
（`hidden aria-hidden="true"`，与 `_fragments/task_photos.html` 同一套做法），
配合 `td.photo-cell { white-space: nowrap }` 保证附图永远只占一行，
超出部分以「+N」提示，点开进灯箱仍能翻完这一笔的全部凭证。
因此 `collect()` 的 `[data-photo-group]` 分支**不能**过滤「没 img 的锚点」。

本文件用「两次真实渲染 + 若干静态文本断言」把这些点锁住，防止改回去。
"""
from __future__ import annotations

import re
from pathlib import Path

from starlette.requests import Request

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "app" / "templates"
CSS = ROOT / "app" / "static" / "app.css"

# 表格行内放凭证缩略图的模板 → 期望的凭证单元格数量
GROUPED_TEMPLATES = {
    "costs/entries.html": 1,       # 成本明细（用户报的位置）
    "payments/list.html": 1,       # 收款流水
    "contracts/detail.html": 2,    # 成本行 + 收款行
    "customers/detail.html": 2,    # 收款明细 + 成本明细
}

MAX_VISIBLE = 3  # 一行最多显示几张，与施工看板一致


def _fake_request(path: str = "/costs") -> Request:
    return Request(
        {
            "type": "http", "method": "GET", "path": path,
            "raw_path": path.encode(), "root_path": "", "scheme": "http",
            "server": ("127.0.0.1", 8090), "client": ("127.0.0.1", 12345),
            "headers": [(b"user-agent", b"pytest")], "query_string": b"",
        }
    )


def _cells(html: str) -> list[str]:
    """取出所有凭证单元格的内部 HTML。"""
    return re.findall(r'<td class="photo-cell" data-photo-group>(.*?)</td>', html, re.S)


def _cell_photo_ids(cell: str) -> tuple[list[int], list[int]]:
    """返回 (有缩略图可见的 id 列表, 无 img 的隐藏锚点 id 列表)。"""
    visible = [int(x) for x in re.findall(r'href="/photos/(\d+)"[^>]*><img class="mini"', cell)]
    hidden = [int(x) for x in re.findall(r'href="/photos/(\d+)" hidden aria-hidden="true"', cell)]
    return visible, hidden


def _seed_costs(db, admin, today, spec):
    """spec: [照片张数, ...]，每条对应一笔成本记录；返回 [[photo_id...], ...]。"""
    from app.models import Contract, ContractCost, Customer, Photo

    out = []
    for i, nph in enumerate(spec):
        cust = Customer(name=f"分组测试客户{i}", owner_id=admin.id, status="active")
        db.add(cust); db.flush()
        c = Contract(customer_id=cust.id, no=f"PG-{i}", sign_date=today,
                     total_amount=10000, status="active")
        db.add(c); db.flush()
        cost = ContractCost(contract_id=c.id, category="材料", amount=100.0 + i,
                            spent_at=today, created_by=admin.id)
        db.add(cost); db.flush()
        pids = []
        for k in range(nph):
            ph = Photo(customer_id=cust.id, contract_id=c.id, cost_id=cost.id,
                       kind="cost", path=f"pg/{i}_{k}.jpg",
                       orig_name=f"票据{i}-{k}.jpg", created_at="2026-01-01T00:00:00")
            db.add(ph); db.flush()
            pids.append(ph.id)
        out.append(pids)
    return out


def _seed_payments(db, admin, today, spec):
    """spec: [截图张数, ...]，每条对应一笔收款；返回 [[photo_id...], ...]。"""
    from app.models import Contract, Customer, Payment, Photo

    out = []
    for i, nph in enumerate(spec):
        cust = Customer(name=f"收款分组客户{i}", owner_id=admin.id, status="active")
        db.add(cust); db.flush()
        c = Contract(customer_id=cust.id, no=f"PP-{i}", sign_date=today,
                     total_amount=20000, status="active")
        db.add(c); db.flush()
        pay = Payment(contract_id=c.id, amount=1000.0 * (i + 1), paid_at=today,
                      method="微信", voucher_no=f"V{i}", received_by=admin.id)
        db.add(pay); db.flush()
        pids = []
        for k in range(nph):
            ph = Photo(customer_id=cust.id, contract_id=c.id, payment_id=pay.id,
                       kind="payment", path=f"pp/{i}_{k}.jpg",
                       orig_name=f"截图{i}-{k}.jpg", created_at="2026-01-01T00:00:00")
            db.add(ph); db.flush()
            pids.append(ph.id)
        out.append(pids)
    return out


def _assert_grouping(cells, seeded, label):
    """每个单元格只含自己那一笔的照片；前 3 张可见，其余是无 img 的隐藏锚点。"""
    assert len(cells) == len(seeded), f"{label}: 应有 {len(seeded)} 个凭证单元格，实际 {len(cells)}"

    got = []
    for cell in cells:
        visible, hidden = _cell_photo_ids(cell)
        assert len(visible) <= MAX_VISIBLE, f"{label}: 一行只应显示 {MAX_VISIBLE} 张，实际 {len(visible)}"
        assert len(hidden) == max(0, len(visible) + len(hidden) - MAX_VISIBLE)
        assert not re.search(r"photo-link extra", cell), "旧方案（.extra 隐藏但有 img）应已移除"
        got.append(sorted(visible + hidden))

    assert sorted(got) == sorted(sorted(p) for p in seeded), (
        f"{label}: 单元格里的照片串组了 —— 灯箱翻页会翻到别的记录"
    )


def test_cost_entries_renders_one_group_per_record(db, admin, today):
    """成本明细：每条记录一个分组，附图一行、超出折叠。"""
    from app.routers.costs import entries

    seeded = _seed_costs(db, admin, today, [2, 5])
    resp = entries(request=_fake_request(), db=db, user=admin)
    html = bytes(resp.body).decode()

    cells = _cells(html)
    _assert_grouping(cells, seeded, "成本明细")

    # 5 张那组：3 张可见 + 2 个隐藏锚点，并提示 +2
    five = next(c for c in cells if len(_cell_photo_ids(c)[0]) == 3)
    assert len(_cell_photo_ids(five)[1]) == 2
    assert re.search(r'class="muted small"[^>]*>\+2<', html), "超过 3 张时应显示 +2 提示"
    # 2 张那组：没有隐藏锚点
    two = next(c for c in cells if len(_cell_photo_ids(c)[0]) == 2)
    assert _cell_photo_ids(two)[1] == []


def test_payments_list_renders_one_group_per_record(db, admin, today):
    """收款流水：每笔收款一个分组，付款截图一行、超出折叠。"""
    from app.routers.payments import list_payments

    seeded = _seed_payments(db, admin, today, [2, 5])
    resp = list_payments(request=_fake_request("/payments"), db=db, user=admin)
    html = bytes(resp.body).decode()

    cells = _cells(html)
    _assert_grouping(cells, seeded, "收款流水")
    assert re.search(r'class="muted small"[^>]*>\+2<', html), "超过 3 张时应显示 +2 提示"


def test_lightbox_collect_handles_photo_group_before_global_fallback():
    """collect() 必须认 [data-photo-group]，排在「整页遍历」兜底之前，且不过滤无 img 锚点。"""
    js = (TPL / "layout.html").read_text()
    branch = js.find("a.closest('[data-photo-group]')")
    fallback = js.find("document.querySelectorAll(ANCHOR_SEL)")
    assert branch != -1, "collect() 缺少 [data-photo-group] 分支（灯箱会串到别的记录）"
    assert fallback != -1, "整页兜底分支不见了（孤立缩略图会退化成只能看 1 张）"
    assert branch < fallback, "[data-photo-group] 分支必须早于整页兜底，否则等于没写"
    # 这一分支必须原样收集（不能 .filter(x => x.querySelector('img'))），
    # 否则第 4 张起那些「无 img 的隐藏锚点」会被过滤，灯箱只能翻 3 张。
    seg = js[branch:fallback]
    assert "querySelector('img')" not in seg, (
        "[data-photo-group] 分支不能过滤无 img 的锚点，否则灯箱翻不到第 4 张以后"
    )


def test_grouped_templates_mark_photo_cells():
    """表格行内的凭证单元格都标了 photo-cell + data-photo-group。"""
    for rel, expect in GROUPED_TEMPLATES.items():
        src = (TPL / rel).read_text()
        n = src.count('<td class="photo-cell" data-photo-group>')
        assert n == expect, f"{rel}: 期望 {expect} 个 photo-cell 单元格，实际 {n}"


def test_grouped_templates_fold_photos_beyond_three():
    """前 3 张渲染 + 第 4 张起无 img 隐藏锚点（与施工看板同一套做法）。"""
    for rel in GROUPED_TEMPLATES:
        src = (TPL / rel).read_text()
        assert "phs[:3]" in src, f"{rel}: 列表应只渲染前 3 张"
        assert "phs[3:]" in src, f"{rel}: 第 4 张起应写成隐藏锚点保留在 DOM 里"
        assert 'hidden aria-hidden="true"' in src, f"{rel}: 隐藏锚点缺 hidden/aria-hidden"
        assert "photo-link extra" not in src, f"{rel}: 旧的 .extra 方案应已移除"


def test_css_keeps_photo_cell_on_one_line():
    """凭证单元格强制一行（否则列宽一紧就折成两三行，行高被撑起来）。"""
    css = CSS.read_text()
    assert re.search(r"td\.photo-cell\s*\{[^}]*white-space:\s*nowrap", css), (
        "缺少 td.photo-cell { white-space: nowrap }，附图会折成多行"
    )
