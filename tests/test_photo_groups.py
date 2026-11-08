"""灯箱翻页分组（`data-photo-group`）的守卫测试。

**背景（v1.12.2 修复的 bug）**：在「导航 → 成本 → 成本明细」点开某笔记录的
凭证缩略图，灯箱里按「下一张」会翻到**别的记录**的凭证。根因是
`app/templates/layout.html` 的 `collect(a)` 在没有 `.gallery` / `.task-photos` /
`.case-thumbs` 容器时，兜底成了「整页所有 `a.photo-link`」——表格单元格里的
凭证缩略图正好落进这个兜底。

**修法有两半，缺一不可**：
  A. 模板：表格行内放凭证的 `<td>` 标上 `data-photo-group`；
  B. `collect()` 先认 `[data-photo-group]`，只在本组内翻页，整页遍历降为最后一级兜底。

**顺带修的第二半坑**：原来靠 `phs[:3]` 截断，第 4 张压根不在 DOM 里，
灯箱翻不到。改成全量渲染 + `.photo-link.extra { display: none }`——
`display:none` 的元素仍会被 `querySelectorAll` 收到，所以灯箱能翻完这一笔的全部凭证，
而单元格宽度不变。

本文件用「一次真实渲染 + 若干静态文本断言」把上面四点锁住，防止改回去。
"""
from __future__ import annotations

import re
from pathlib import Path

from starlette.requests import Request

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "app" / "templates"
CSS = ROOT / "app" / "static" / "app.css"

# 表格行内放凭证缩略图的模板 → 期望的 `data-photo-group` 单元格数量
GROUPED_TEMPLATES = {
    "costs/entries.html": 1,       # 成本明细（用户报的位置）
    "payments/list.html": 1,       # 收款/退款列表
    "contracts/detail.html": 2,    # 成本行 + 收款行
    "customers/detail.html": 2,    # 付款行 + 成本行
}


def _fake_request(path: str = "/costs") -> Request:
    return Request(
        {
            "type": "http", "method": "GET", "path": path,
            "raw_path": path.encode(), "root_path": "", "scheme": "http",
            "server": ("127.0.0.1", 8090), "client": ("127.0.0.1", 12345),
            "headers": [(b"user-agent", b"pytest")], "query_string": b"",
        }
    )


def _seed(db, admin, today, spec):
    """spec: [(照片张数), ...]，每条对应一笔成本记录；返回 [(cost_id, [photo_id...]), ...]。"""
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
        out.append((cost.id, pids))
    return out


def test_cost_entries_renders_one_group_per_record(db, admin, today):
    """每条成本记录一个分组；组内只有自己的凭证；>3 张时多出的用 .extra 隐藏而非丢弃。"""
    from app.routers.costs import entries

    seeded = _seed(db, admin, today, [2, 4])
    resp = entries(request=_fake_request(), db=db, user=admin)
    html = bytes(resp.body).decode()

    cells = re.findall(r"<td data-photo-group>(.*?)</td>", html, re.S)
    assert len(cells) == 2, f"应有 2 个凭证单元格，实际 {len(cells)}"

    # 单元格渲染顺序是按时间倒序，用「照片 id 集合」比对，不依赖顺序
    got_sets = []
    for cell in cells:
        got_sets.append([int(x) for x in re.findall(r"/photos/(\d+)/thumb", cell)])
    expected = [pids for _, pids in seeded]
    assert sorted(map(sorted, got_sets)) == sorted(map(sorted, expected)), (
        "单元格里的照片串组了：灯箱翻页会翻到别的记录的凭证"
    )

    # 4 张那组：全部渲染，第 4 张带 extra（CSS 隐藏，灯箱仍可翻到）
    four = next(c for c in cells if len(re.findall(r"/photos/\d+/thumb", c)) == 4)
    assert four.count("photo-link extra") == 1
    # 2 张那组：没有 extra
    two = next(c for c in cells if len(re.findall(r"/photos/\d+/thumb", c)) == 2)
    assert "photo-link extra" not in two
    # 截断提示保留
    assert re.search(r'class="muted small">\+1<', html), "超过 3 张时应显示 +N 提示"


def test_lightbox_collect_handles_photo_group_before_global_fallback():
    """collect() 必须认 [data-photo-group]，且排在「整页遍历」兜底之前。"""
    js = (TPL / "layout.html").read_text()
    branch = js.find("a.closest('[data-photo-group]')")
    fallback = js.find("document.querySelectorAll(ANCHOR_SEL)")
    assert branch != -1, "collect() 缺少 [data-photo-group] 分支（灯箱会串到别的记录）"
    assert fallback != -1, "整页兜底分支不见了（孤立缩略图会退化成只能看 1 张）"
    assert branch < fallback, "[data-photo-group] 分支必须早于整页兜底，否则等于没写"


def test_grouped_templates_mark_photo_cells():
    """表格行内的凭证单元格都标了 data-photo-group。"""
    for rel, expect in GROUPED_TEMPLATES.items():
        src = (TPL / rel).read_text()
        n = src.count("<td data-photo-group>")
        assert n == expect, f"{rel}: 期望 {expect} 个 data-photo-group 单元格，实际 {n}"


def test_grouped_templates_no_longer_slice_photos():
    """不再用 phs[:3] 截断（否则灯箱翻不到第 4 张以后）。"""
    offenders = []
    for rel in GROUPED_TEMPLATES:
        if "phs[:3]" in (TPL / rel).read_text():
            offenders.append(rel)
    assert not offenders, f"这些模板还在截断照片列表：{offenders}"


def test_css_hides_extra_photo_links():
    """.extra 靠 display:none 隐藏——DOM 保留，灯箱才翻得到。"""
    css = CSS.read_text()
    assert re.search(r"\.photo-link\.extra\s*\{[^}]*display:\s*none", css), (
        "缺少 .photo-link.extra { display: none }，第 4 张以后会直接显示出来撑宽单元格"
    )
