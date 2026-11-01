"""正文回读校验 + 保存成功判定（P1-7 回归测试）。

背景：`_set_body()` 只检查 `execCommand('insertText')` 返不返 false，
正文内容是否真的写进去完全没验证——而标题 `_set_title()` 一直有回读校验。
对称性缺失意味着一篇「内容残缺」的草稿也可能被标记成功。

`_saved_ok()` 也只看 URL（含 action=edit 且不含 isNew=1），规则一变就失效；
反过来说「已保存但 URL 未更新」会误报失败 → 用户重试 → 重复草稿。
"""
import json

import pytest

import app.services.wxmp as wxmp_mod
from app.services.wxmp import _norm_line, _read_body_lines, _saved_ok, _set_body, JS_BODY_LINES, JS_FOCUS_BODY


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)


class BodyCDP:
    """模拟正文编辑器：insertText 把文字记进 paragraphs，回车新增一段。

    行为由 `swallow` 控制：命中关键字的行会被吞掉（模拟渲染/过滤丢内容）。
    """

    def __init__(self, swallow=()):
        self.swallow = list(swallow)
        self.paragraphs = [""]

    def js(self, expr, **kw):
        if expr == JS_FOCUS_BODY:
            return "ok"
        if expr == JS_BODY_LINES:
            rows = [p for p in self.paragraphs if p.strip()]
            return json.dumps(rows)
        if expr.startswith("document.execCommand('insertText'"):
            # 取出要插入的文字
            payload = expr.split("insertText', false, ", 1)[1].rsplit(")", 1)[0]
            text = json.loads(payload)
            if text in self.swallow:
                return True  # 返回 true 但内容没进去＝最难发现的那种失败
            self.paragraphs[-1] += text
            return True
        return None

    def call(self, method, **params):
        if method == "Input.dispatchKeyEvent" and params.get("type") == "keyDown":
            self.paragraphs.append("")
        return {}


def test_norm_line_handles_fullwidth_space():
    assert _norm_line("▍工地位置\u3000A栋") == "▍工地位置 A栋"
    assert _norm_line("  x  ") == "x"
    assert _norm_line(None) == ""


def test_read_body_lines_parses_json():
    class C:
        def js(self, expr, **kw):
            return json.dumps(["行1", "行2"]) if expr == JS_BODY_LINES else None

    assert _read_body_lines(C()) == ["行1", "行2"]


def test_read_body_lines_handles_garbage():
    class C:
        def js(self, expr, **kw):
            return "not-json"

    assert _read_body_lines(C()) == []


def test_set_body_passes_when_all_lines_written():
    c = BodyCDP()
    _set_body(c, ["▍工地位置\u3000A栋", "▍施工时间\u30002026-10-27"])
    # 不抛异常即通过
    assert "▍工地位置 A栋" in _norm_line(c.paragraphs[0])


def test_set_body_raises_when_line_swallowed():
    """核心回归：某行被吞掉时必须报错，而不是静默发一篇残缺草稿。"""
    c = BodyCDP(swallow=["▍施工工序\u3000前期施工"])
    with pytest.raises(RuntimeError) as ei:
        _set_body(c, ["▍工地位置\u3000A栋", "▍施工工序\u3000前期施工", "▍产品类型\u3000暖气片"])
    msg = str(ei.value)
    assert "正文回读校验失败" in msg
    assert "前期施工" in msg


def test_set_body_tolerates_blank_lines():
    """空串元素是空段落，不应参与比对。"""
    c = BodyCDP()
    _set_body(c, ["行A", "", "行B"])
    assert "行A" in _norm_line(c.paragraphs[0])


class SaveCDP:
    def __init__(self, url="https://mp.weixin.qq.com/cgi-bin/appmsg?action=edit&isNew=1&token=1",
                 has_msg=False, title=""):
        self._url = url
        self._has_msg = has_msg
        self._title = title
        self.calls = 0

    def js(self, expr, **kw):
        if "hasMsg" in expr:
            self.calls += 1
            return json.dumps({
                "url": self._url,
                "hasMsg": self._has_msg,
                "title": self._title,
            })
        if expr == "location.href":
            return self._url
        return None


def test_saved_ok_url_no_longer_isnew():
    """原有判据仍生效：URL 去掉了 isNew=1。"""
    c = SaveCDP(url="https://mp.weixin.qq.com/cgi-bin/appmsg?action=edit&token=1")
    assert _saved_ok(c) is True


def test_saved_ok_appmsgid(monkeypatch):
    """新判据：URL 带 appmsgid（已生成草稿 id），即便 isNew=1 也算成功。"""
    c = SaveCDP(url="https://mp.weixin.qq.com/cgi-bin/appmsg?action=edit&isNew=1&appmsgid=100001")
    assert _saved_ok(c) is True


def test_saved_ok_via_message_and_title():
    """新判据：出现「已保存」提示 + 标题未丢。"""
    c = SaveCDP(has_msg=True, title="暖气片施工案例 · 育才苑二期 8栋")
    assert _saved_ok(c, expect_title="暖气片施工案例 · 育才苑二期 8栋") is True


def test_saved_ok_message_but_title_changed():
    """有提示但标题对不上（内容可能被丢）→ 不算成功。"""
    c = SaveCDP(has_msg=True, title="完全不同的标题")
    assert _saved_ok(c, expect_title="期望的标题") is False


def test_saved_ok_still_new_unsaved():
    """全新未保存的编辑页 → 不算成功。"""
    c = SaveCDP(has_msg=False)
    assert _saved_ok(c) is False
