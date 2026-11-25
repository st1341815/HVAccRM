"""电子合同 / 结算单的文档渲染。

两条路线：
1. **电子合同** —— 用户维护的模板正文（HTML + `{{占位符}}`），这里做纯字符串替换；
2. **结算单** —— 固定排版（模板由代码给），数据同样来自这里。

刻意不用 Jinja 渲染用户内容：模板是人在后台写的，写坏一个 `{%` 就让整页 500。
字符串替换最差也只是少填几个值，纸上不好看但不会崩。

外币、千分位、大写金额这些合同里必须写对的东西都在这里集中处理。
"""
from __future__ import annotations

import re
from datetime import datetime
from html import escape
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Contract, ContractTemplate, User
from . import costs as cost_svc
from . import finance as finance_svc
from . import settings as settings_svc

# ---------------------------------------------------------------- 设置项
_COMPANY_KEYS = (
    ("doc.company_name", "公司名称（合同里的乙方）"),
    ("doc.company_phone", "公司电话"),
    ("doc.company_address", "公司地址"),
    ("doc.company_bank", "开户行"),
    ("doc.company_account", "账号"),
)


def company_info(db: Session) -> dict[str, str]:
    """合同抬头（乙方）信息，后台可改。默认空串——没填就在合同上留空，由用户自己补。"""
    raw = settings_svc.all_settings(db)
    return {k: (raw.get(k) or "") for k, _ in _COMPANY_KEYS}


def company_labels() -> list[tuple[str, str]]:
    return list(_COMPANY_KEYS)


def save_company_info(db: Session, data: dict[str, str]) -> None:
    for key, _ in _COMPANY_KEYS:
        settings_svc.set_(db, key, (data.get(key) or "").strip())


# ---------------------------------------------------------------- 模板读写
def list_templates(db: Session) -> list[ContractTemplate]:
    return list(
        db.scalars(select(ContractTemplate).order_by(ContractTemplate.is_default.desc(), ContractTemplate.id)).all()
    )


def default_template(db: Session) -> ContractTemplate | None:
    row = db.scalars(select(ContractTemplate).where(ContractTemplate.is_default == 1).limit(1)).first()
    return row or db.scalars(select(ContractTemplate).order_by(ContractTemplate.id).limit(1)).first()


def pick_template(db: Session, template_id: int | None = None) -> ContractTemplate | None:
    """打印时选模板：指定了就用指定的（校验存在且启用），否则用默认那份。"""
    if template_id:
        row = db.get(ContractTemplate, template_id)
        if row is not None and row.is_active:
            return row
    return default_template(db)


def ensure_default_template(db: Session, created_by: int | None = None) -> ContractTemplate:
    """库里一份模板都没有时，落地内置范本——开箱就能打，不至于先让用户写一份。"""
    row = default_template(db)
    if row is not None:
        return row
    row = ContractTemplate(
        name="标准工程合同（内置范本）",
        content=DEFAULT_TEMPLATE,
        is_default=1,
        is_active=1,
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def set_default(db: Session, template_id: int) -> None:
    for row in db.scalars(select(ContractTemplate)).all():
        row.is_default = 1 if row.id == template_id else 0


# ---------------------------------------------------------------- 金额
def money(v: Any) -> str:
    try:
        return f"{float(v or 0):,.2f}"
    except (TypeError, ValueError):
        return "0.00"


_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_UNITS = ("", "拾", "佰", "仟")
_BIG = ("", "万", "亿", "万亿")


def rmb_upper(value: Any) -> str:
    """人民币大写。合同里必须写对：12800 → 壹万贰仟捌佰元整。

    负数写「负」，零写「零元整」；小数按角分处理，无角无分补「整」。
    """
    try:
        amount = round(float(value or 0), 2)
    except (TypeError, ValueError):
        amount = 0.0
    if abs(amount) < 0.005:
        return "零元整"
    sign = "负" if amount < 0 else ""
    amount = abs(amount)
    integer = int(amount)
    cents = int(round((amount - integer) * 100))
    if cents == 100:  # 四舍五入后进位
        integer += 1
        cents = 0

    out = ""
    if integer == 0:
        out = "零"
    else:
        groups: list[int] = []
        n = integer
        while n > 0:
            groups.append(n % 10000)
            n //= 10000
        parts: list[str] = []
        for gi in range(len(groups) - 1, -1, -1):
            g = groups[gi]
            if g == 0:
                # 整组是零：只在后面还有非零组时记一个「零」
                if parts and not parts[-1].endswith("零"):
                    parts.append("零")
                continue
            chunk = ""
            # 跨组补零：低组不足四位且前面已有内容时要补一个「零」
            # （10001 → 壹万零壹，不补就变成「壹万壹」）
            if parts and g < 1000:
                chunk += "零"
            zero_flag = False
            for pos in range(3, -1, -1):
                digit = (g // (10 ** pos)) % 10
                if digit == 0:
                    zero_flag = True
                else:
                    if zero_flag and chunk and not chunk.endswith("零"):
                        chunk += "零"
                    chunk += _DIGITS[digit] + _UNITS[pos]
                    zero_flag = False
            chunk += _BIG[gi]
            # 组内只有一个「1」在千位以外（如 12 元）不写「壹拾」
            parts.append(chunk)
        out = "".join(parts) or "零"
        out = out.rstrip("零")

    result = sign + out + "元"
    if cents == 0:
        return result + "整"
    jiao, fen = divmod(cents, 10)
    if jiao:
        result += _DIGITS[jiao] + "角"
    elif fen:
        result += "零"
    if fen:
        result += _DIGITS[fen] + "分"
    return result


# ---------------------------------------------------------------- 表格片段
_DOC_TABLE = 'class="doc-table"'
_DOC_TH = "<th>"


def items_table(items: Iterable) -> str:
    rows = list(items)
    if not rows:
        return '<p class="doc-empty">（本合同未录入明细）</p>'
    out = [
        f"<table {_DOC_TABLE}><thead><tr>"
        f"{_DOC_TH}产品</th>{_DOC_TH}规格</th>{_DOC_TH}数量</th>"
        f"{_DOC_TH}单位</th>{_DOC_TH}单价</th>{_DOC_TH}金额</th>"
        "</tr></thead><tbody>"
    ]
    total = 0.0
    for it in rows:
        amount = float(it.amount or 0)
        total += amount
        out.append(
            "<tr>"
            f"<td>{escape(str(it.product_name or ''))}</td>"
            f"<td>{escape(str(it.spec or ''))}</td>"
            f"<td class=\"num\">{it.qty if it.qty is not None else ''}</td>"
            f"<td>{escape(str(it.unit or ''))}</td>"
            f"<td class=\"num\">{money(it.unit_price)}</td>"
            f"<td class=\"num\">{money(amount)}</td>"
            "</tr>"
        )
    out.append(
        f'<tr class="sum"><td colspan="5">合计</td>'
        f'<td class="num">{money(total)}</td></tr>'
    )
    out.append("</tbody></table>")
    return "".join(out)


def payments_table(payments: Iterable, users: dict[int, str] | None = None) -> str:
    rows = list(payments)
    if not rows:
        return '<p class="doc-empty">（暂无收款记录）</p>'
    out = [
        f"<table {_DOC_TABLE}><thead><tr>"
        f"{_DOC_TH}收款日期</th>{_DOC_TH}金额</th>{_DOC_TH}方式</th>"
        f"{_DOC_TH}凭证号</th>{_DOC_TH}收款人</th>{_DOC_TH}备注</th>"
        "</tr></thead><tbody>"
    ]
    total = 0.0
    for p in rows:
        amount = float(p.amount or 0)
        total += amount
        who = ""
        if users and getattr(p, "received_by", None):
            who = users.get(p.received_by, "") or ""
        out.append(
            "<tr>"
            f"<td>{escape(str(p.paid_at or ''))}</td>"
            f'<td class="num">{money(amount)}</td>'
            f"<td>{escape(str(p.method or ''))}</td>"
            f"<td>{escape(str(p.voucher_no or ''))}</td>"
            f"<td>{escape(who)}</td>"
            f"<td>{escape(str(p.remark or ''))}</td>"
            "</tr>"
        )
    out.append(
        f'<tr class="sum"><td colspan="1">合计</td>'
        f'<td class="num">{money(total)}</td><td colspan="4"></td></tr>'
    )
    out.append("</tbody></table>")
    return "".join(out)


def costs_table(costs: Iterable) -> str:
    rows = list(costs)
    if not rows:
        return '<p class="doc-empty">（暂无成本记录）</p>'
    out = [
        f"<table {_DOC_TABLE}><thead><tr>"
        f"{_DOC_TH}日期</th>{_DOC_TH}费用项目</th>{_DOC_TH}金额</th>"
        f"{_DOC_TH}供应商</th>{_DOC_TH}安装师傅</th>{_DOC_TH}备注</th>"
        "</tr></thead><tbody>"
    ]
    total = 0.0
    for r in rows:
        amount = float(r.amount or 0)
        total += amount
        supplier = getattr(getattr(r, "supplier", None), "name", "") or ""
        installer = getattr(getattr(r, "installer", None), "display_name", "") or ""
        out.append(
            "<tr>"
            f"<td>{escape(str(r.spent_at or ''))}</td>"
            f"<td>{escape(str(r.category or ''))}</td>"
            f'<td class="num">{money(amount)}</td>'
            f"<td>{escape(supplier)}</td>"
            f"<td>{escape(installer)}</td>"
            f"<td>{escape(str(r.remark or ''))}</td>"
            "</tr>"
        )
    out.append(
        f'<tr class="sum"><td colspan="2">合计</td>'
        f'<td class="num">{money(total)}</td><td colspan="3"></td></tr>'
    )
    out.append("</tbody></table>")
    return "".join(out)


def changes_table(changes: Iterable) -> str:
    rows = list(changes)
    if not rows:
        return '<p class="doc-empty">（无增项）</p>'
    out = [
        f"<table {_DOC_TABLE}><thead><tr>"
        f"{_DOC_TH}增项原因</th>{_DOC_TH}金额</th>{_DOC_TH}审批时间</th>"
        "</tr></thead><tbody>"
    ]
    total = 0.0
    for co in rows:
        amount = float(co.amount or 0)
        total += amount
        out.append(
            "<tr>"
            f"<td>{escape(str(co.reason or ''))}</td>"
            f'<td class="num">{money(amount)}</td>'
            f"<td>{escape(str(co.approved_at or ''))}</td>"
            "</tr>"
        )
    out.append(
        f'<tr class="sum"><td>合计</td><td class="num">{money(total)}</td><td></td></tr>'
    )
    out.append("</tbody></table>")
    return "".join(out)


# ---------------------------------------------------------------- 上下文
def _now_text() -> tuple[str, str]:
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M"), now.strftime("%Y-%m-%d")


def build_context(db: Session, contract: Contract, *, with_cost: bool = False) -> dict[str, str]:
    """把一份合同摊平成 `{{占位符}}` → 文本 的字典。

    键用中文：模板是业务人员维护的，`{{客户名称}}` 比 `{{ customer_name }}` 好懂。
    """
    customer = contract.customer
    summary = finance_svc.contract_summary(db, contract)
    printed_at, printed_date = _now_text()
    company = company_info(db)

    users = {
        u.id: (u.display_name or u.username or "")
        for u in db.scalars(select(User)).all()
    }

    ctx: dict[str, str] = {
        # ---- 合同 ----
        "合同号": contract.no or f"#{contract.id}",
        "签订日": contract.sign_date or "",
        "产品类型": contract.product_type or "",
        "合同状态": contract.status or "",
        "合同备注": escape(str(contract.notes or "")).replace("\n", "<br>"),
        "结算状态": "已结算" if contract.is_settled else "未结算",
        "结算日": contract.settled_at or "",
        # ---- 金额 ----
        "合同金额": money(contract.total_amount),
        "优惠": money(contract.discount or 0),
        "合同额": money(summary.base),
        "增项合计": money(summary.change),
        "应收总额": money(summary.total),
        "已收金额": money(summary.received),
        "未收金额": money(summary.outstanding),
        "回款率": f"{summary.recovery_rate}",
        "金额大写": rmb_upper(summary.total),
        "合同金额大写": rmb_upper(contract.total_amount),
        # ---- 客户（合同里的甲方）----
        "客户名称": escape(str(getattr(customer, "name", "") or "")),
        "客户电话": escape(str(getattr(customer, "phone", "") or "")),
        "客户微信": escape(str(getattr(customer, "wechat", "") or "")),
        "客户地址": escape(str(getattr(customer, "address", "") or "")),
        "客户类型": escape(str(getattr(customer, "type", "") or "")),
        "甲方名称": escape(str(getattr(customer, "name", "") or "")),
        "甲方电话": escape(str(getattr(customer, "phone", "") or "")),
        "甲方地址": escape(str(getattr(customer, "address", "") or "")),
        "乙方名称": escape(company.get("doc.company_name", "")),
        "乙方电话": escape(company.get("doc.company_phone", "")),
        "乙方地址": escape(company.get("doc.company_address", "")),
        "开户行": escape(company.get("doc.company_bank", "")),
        "账号": escape(company.get("doc.company_account", "")),
        # ---- 表格 ----
        "明细表": items_table(contract.items),
        "收款记录": payments_table(
            sorted(contract.payments, key=lambda p: (p.paid_at or "", p.id)), users
        ),
        "增项记录": changes_table(contract.change_orders),
        # ---- 其它 ----
        "打印时间": printed_at,
        "打印日期": printed_date,
    }
    if with_cost:
        profit = cost_svc.profit_summary(db, contract)
        ctx.update(
            {
                "成本合计": money(profit.cost),
                "毛利": money(profit.gross_profit),
                "毛利率": f"{profit.gross_margin}",
                "成本明细": costs_table(contract.costs),
            }
        )
    return ctx


PLACEHOLDER_RE = re.compile(r"\{\{\s*([^{}\s]+)\s*\}\}")


def render_text(text: str, ctx: dict[str, str], missing: list[str] | None = None) -> str:
    """替换 `{{键}}`。未识别的键**替换成空串**并把键名记进 missing。

    留空而不是保留原样：保留的话纸面上会出现一行 `{{客户名称}}`，比空缺更难看。
    后台预览会把这些键列出来，提示改模板。
    """

    def _sub(m: re.Match) -> str:
        key = m.group(1)
        if key in ctx:
            return ctx[key]
        if missing is not None and key not in missing:
            missing.append(key)
        return ""

    return PLACEHOLDER_RE.sub(_sub, text)


def render_contract_body(db: Session, contract: Contract, template: ContractTemplate | None = None) -> tuple[str, list[str]]:
    """渲染电子合同正文，返回 (html, 未识别变量名列表)。"""
    tpl = template or default_template(db) or ensure_default_template(db)
    ctx = build_context(db, contract, with_cost=False)
    missing: list[str] = []
    return render_text(tpl.content, ctx, missing), missing


def available_vars() -> list[tuple[str, str]]:
    """后台展示的变量清单（键, 说明），与 build_context 保持同步。"""
    return [
        ("合同号", "合同编号"),
        ("签订日", "签订日期"),
        ("产品类型", "产品类型"),
        ("合同状态", "active / done / void"),
        ("合同备注", "合同备注"),
        ("结算状态", "已结算 / 未结算"),
        ("结算日", "标记结算的日期"),
        ("合同金额", "录入的合同总额"),
        ("优惠", "优惠金额"),
        ("合同额", "合同额（减优惠）"),
        ("增项合计", "增项合计"),
        ("应收总额", "应收总额（含增项）"),
        ("已收金额", "已收金额"),
        ("未收金额", "未收金额"),
        ("回款率", "回款率 %"),
        ("金额大写", "应收总额的人民币大写"),
        ("合同金额大写", "合同金额的人民币大写"),
        ("客户名称", "客户名"),
        ("客户电话", "客户手机号"),
        ("客户微信", "客户微信"),
        ("客户地址", "客户地址"),
        ("客户类型", "家装 / 工程 / 经销商"),
        ("甲方名称", "同客户名称"),
        ("甲方电话", "同客户电话"),
        ("甲方地址", "同客户地址"),
        ("乙方名称", "公司名称（后台设置）"),
        ("乙方电话", "公司电话"),
        ("乙方地址", "公司地址"),
        ("开户行", "收款开户行"),
        ("账号", "收款账号"),
        ("明细表", "合同明细表格（HTML）"),
        ("收款记录", "收款流水表格（HTML）"),
        ("增项记录", "增项表格（HTML）"),
        ("打印时间", "YYYY-MM-DD HH:MM"),
        ("打印日期", "YYYY-MM-DD"),
    ]


# ---------------------------------------------------------------- 内置范本
DEFAULT_TEMPLATE = """<div class="doc-head">
  <h1>暖通空调工程合同</h1>
  <div class="doc-no">合同编号：{{合同号}}</div>
</div>

<div class="doc-parties">
  <div><span class="lbl">甲方（发包方）：</span>{{甲方名称}}</div>
  <div><span class="lbl">联系电话：</span>{{甲方电话}}</div>
  <div><span class="lbl">施工地址：</span>{{甲方地址}}</div>
  <div><span class="lbl">乙方（承包方）：</span>{{乙方名称}}</div>
  <div><span class="lbl">联系电话：</span>{{乙方电话}}</div>
  <div><span class="lbl">联系地址：</span>{{乙方地址}}</div>
</div>

<p>根据《中华人民共和国民法典》及相关法律法规，甲乙双方在平等、自愿、公平、诚实信用的
基础上，就甲方的暖通空调（中央空调 / 地暖 / 暖气片 / 新风 / 净水）工程事宜协商一致，
订立本合同，共同遵守。</p>

<h2>第一条　工程概况</h2>
<div class="doc-meta">
  <div><span class="lbl">产品类型：</span>{{产品类型}}</div>
  <div><span class="lbl">签订日期：</span>{{签订日}}</div>
  <div><span class="lbl">工程地址：</span>{{甲方地址}}</div>
</div>
<p>具体设备、材料、数量与单价见下表：</p>
{{明细表}}

<h2>第二条　合同价款</h2>
<div class="doc-meta">
  <div><span class="lbl">合同金额：</span>￥{{合同金额}}</div>
  <div><span class="lbl">增项合计：</span>￥{{增项合计}}</div>
  <div><span class="lbl">应收总额：</span>￥{{应收总额}}</div>
</div>
<p><strong>人民币大写：{{金额大写}}</strong></p>
<p>上述价款包含设备、辅材、运输、安装、调试及税金。甲方确认增项（变更单）后按实际发生额结算。</p>

<h2>第三条　付款方式</h2>
<p>1. 本合同签订之日起三日内，甲方支付合同金额的 60% 作为预付款；</p>
<p>2. 设备进场、隐蔽工程验收合格后，甲方支付合同金额的 35%；</p>
<p>3. 工程竣工验收合格后七日内，甲方支付剩余 5% 尾款；</p>
<p>4. 增项（变更单）款项随尾款一并结清。</p>
<p>收款账户：{{开户行}}　户名：{{乙方名称}}　账号：{{账号}}</p>

<h2>第四条　工期</h2>
<p>1. 开工日期以甲方具备进场条件并书面（含微信）通知乙方之日起计算；</p>
<p>2. 标准户型施工工期约为 7 至 15 个工作日，具体以现场条件与双方确认的施工计划为准；</p>
<p>3. 因甲方原因（未提供水电、场地未腾空、材料变更等）或不可抗力导致停工的，工期顺延。</p>

<h2>第五条　双方权利义务</h2>
<p>1. 甲方应按约定支付款项，并提供施工所需的水、电及场地条件，及时确认施工方案与增项；</p>
<p>2. 乙方应按图施工、使用约定品牌型号的设备与材料，保证施工质量，遵守现场管理规定；</p>
<p>3. 乙方施工人员应持证上岗，施工期间的安全责任由乙方承担；</p>
<p>4. 甲方擅自变更设计或自行改动已完工部分的，由此产生的费用与后果由甲方承担。</p>

<h2>第六条　验收</h2>
<p>1. 隐蔽工程（管道、线路）完成后，乙方通知甲方验收，甲方应在收到通知后 24 小时内验收；</p>
<p>2. 逾期未验收且未提出异议的，视为验收合格；</p>
<p>3. 工程整体完工后进行竣工验收，双方签署验收单。验收不合格的，乙方应无偿整改后重新验收。</p>

<h2>第七条　质保与售后</h2>
<p>1. 设备质保按厂家规定执行（整机一般 1 至 6 年，以厂家保修卡为准）；</p>
<p>2. 安装工程质保 2 年，质保期内因施工质量造成的问题，乙方免费维修；</p>
<p>3. 下列情形不在免费保修范围：人为损坏、擅自拆改、非乙方提供的材料、不可抗力。</p>

<h2>第八条　违约责任</h2>
<p>1. 甲方逾期付款的，每逾期一日按未付金额的 0.3% 支付违约金；</p>
<p>2. 乙方逾期完工的，每逾期一日按合同金额的 0.3% 支付违约金；</p>
<p>3. 任何一方无故解除合同的，应向对方支付合同金额 10% 的违约金，并赔偿实际损失。</p>

<h2>第九条　争议解决</h2>
<p>本合同履行中发生争议，双方应友好协商解决；协商不成的，任何一方可向工程所在地人民法院起诉。</p>

<h2>第十条　其他</h2>
<p>1. 本合同一式两份，甲乙双方各执一份，自双方签字（盖章）之日起生效；</p>
<p>2. 合同附件（明细表、变更单、验收单）与本合同具有同等法律效力；</p>
<p>3. 未尽事宜由双方另行签订补充协议。</p>
<p class="doc-note">合同备注：{{合同备注}}</p>

<div class="doc-sign">
  <div class="sign-box">
    <div class="sign-title">甲方（签字 / 盖章）</div>
    <div class="sign-line">签字：</div>
    <div class="sign-line">日期：　　　年　　月　　日</div>
  </div>
  <div class="sign-box">
    <div class="sign-title">乙方（签字 / 盖章）</div>
    <div class="sign-line">签字：</div>
    <div class="sign-line">日期：　　　年　　月　　日</div>
  </div>
</div>
"""
