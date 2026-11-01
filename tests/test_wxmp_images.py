"""公众号图片上传的逐张判定与失败早退（P1-1 / P1-2 回归测试）。

背景：旧实现用「累计 imgs 数 >= 第 i 张」判定，第 k 张一失败后 imgs 永远落后，
后面每张都追不上 target，白跑满 per_image_timeout —— 19 张图最坏 ~28.5 分钟，
期间 _LOCK 一直被占，别人同步/登录全阻塞。

修复：逐张用「增量」判定（上传后 imgs/cdn 是否比上传前涨了）+ 连续失败早退。
"""
import json

import pytest

import app.services.wxmp as wxmp_mod


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """把 time.sleep 变成 no-op：这些用例不需要真等待，纯逻辑验证。"""
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)


class FakeCDP:
    """模拟 CDP 的最小接口，用 fail_indices 指定哪些张上传失败。"""

    def __init__(self, fail_indices=()):
        self.fail = set(fail_indices)
        self.imgs = 0
        self.cdn = 0
        self.n = 0  # set_files 被调用次数

    def js(self, expr, **kw):
        if "qpic.cn" in expr:  # JS_PROBE
            return json.dumps(
                {"ready": True, "imgs": self.imgs, "cdn": self.cdn, "text": ""}
            )
        return "ok"  # JS_FOCUS_BODY

    def set_files(self, selector, files):
        self.n += 1
        if self.n not in self.fail:
            self.imgs += 1
            self.cdn += 1


T = 0.05  # 每张超时；循环里 sleep(0.8) 保证每张恰好探测一次


def _files(n):
    return [f"/tmp/{i}.jpg" for i in range(1, n + 1)]


def test_all_images_succeed():
    c = FakeCDP()
    st, missing = wxmp_mod._insert_images(c, _files(7), per_image_timeout=T, batch_size=100)
    assert st["imgs"] == 7 and st["cdn"] == 7
    assert missing == []


def test_sporadic_failure_keeps_going():
    """偶发单张失败不应拖垮后面每一张（这是旧实现的核心 bug）。"""
    c = FakeCDP(fail_indices={3})
    st, missing = wxmp_mod._insert_images(c, _files(6), per_image_timeout=T, batch_size=100)
    assert [m["index"] for m in missing] == [3]
    assert c.n == 6, "偶发失败后仍应把剩余图片都尝试一遍"


def test_consecutive_failures_abort_early():
    """连续 2 张失败 → 立即中止，剩余标记 skipped（不再空跑）。"""
    c = FakeCDP(fail_indices={3, 4})
    st, missing = wxmp_mod._insert_images(c, _files(8), per_image_timeout=T, batch_size=100)
    assert [m["index"] for m in missing] == [3, 4, 5, 6, 7, 8]
    assert missing[0]["reason"] == "upload_timeout"
    assert missing[2]["reason"] == "skipped_after_consecutive_failures"
    assert c.n == 4, "8 张图在第 3、4 张连续失败后应只尝试 4 次"


def test_total_failure_does_not_scan_all():
    """19 张全失败时只试 2 次（旧实现会 19 × 90s ≈ 28.5 分钟）。"""
    c = FakeCDP(fail_indices=set(range(1, 20)))
    st, missing = wxmp_mod._insert_images(c, _files(19), per_image_timeout=T, batch_size=100)
    assert c.n == 2
    assert len(missing) == 19


def test_empty_image_list():
    c = FakeCDP()
    st, missing = wxmp_mod._insert_images(c, [], per_image_timeout=T)
    assert missing == [] and c.n == 0
