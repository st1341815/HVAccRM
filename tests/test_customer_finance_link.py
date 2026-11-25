"""客户详情页「合同与收款」tab：产品列必须链到合同详情。

**背景**（v1.13.2 需求）：客户页上看到「这单是什么产品」，下一句多半就是
「合同怎么签的 / 成本多少 / 收了多少钱」——都得进合同详情。产品单元格此前是裸文本，
用户得自己回「合同」列表再搜一遍。

这里钉住两条约定：
1. 产品列渲染成指向 `/contracts/{id}` 的链接（`plan_rows` 里的 `c` 就是合同对象）；
2. `product_type` 为空时退回合同编号 —— 不摆一个点不动的「-」，
   否则「没填产品」的合同在这一列上直接失去入口。
"""
from __future__ import annotations

from pathlib import Path

TPL = Path(__file__).resolve().parent.parent / "app" / "templates"
DETAIL = TPL / "customers" / "detail.html"


def _finance_block() -> str:
    """取出 `tab == 'finance'` 那一段，避免误伤客户页其它「产品」字样（如意向产品）。"""
    html = DETAIL.read_text()
    start = html.index("{% elif tab == 'finance' %}")
    end = html.index("{% elif tab == 'payments' %}")
    return html[start:end]


def test_product_cell_links_to_contract_detail():
    block = _finance_block()
    assert 'href="/contracts/{{ c.id }}"' in block, (
        "「合同与收款」tab 的产品列必须链到 /contracts/{id}，否则用户还得自己去合同列表找"
    )


def test_product_cell_is_no_longer_bare_text():
    block = _finance_block()
    assert "{{ c.product_type or '-' }}" not in block, (
        "产品单元格不应再是裸文本（那正是本次要修掉的写法）"
    )


def test_empty_product_falls_back_to_contract_no():
    """产品为空的合同：链接文字退回合同编号，保证这一格永远可点。"""
    block = _finance_block()
    assert "c.product_type" in block and "{{ c.no }}" in block, (
        "product_type 为空时要退回 {{ c.no }} 作为链接文字"
    )
