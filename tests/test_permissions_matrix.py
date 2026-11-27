"""权限矩阵自定义（v1.17.0）+ 权限点中文释义。

覆盖三类风险：
1. 权限点目录与 ALL_PERMISSIONS 不同步（加了权限点忘了写中文释义）；
2. 自定义矩阵保存后不生效（SessionLocal autoflush=False 导致的读旧值）；
3. admin 被误降权后后台再也进不去。
"""
from __future__ import annotations

import pytest

from app import permissions as P


@pytest.fixture(autouse=True)
def _clean_cache():
    P.reset_cache()
    yield
    P.reset_cache()


class _U:
    def __init__(self, role: str):
        self.role = role


# ------------------------------------------------------------ 权限点目录
def test_catalog_covers_all_permissions():
    keys = [k for k, _l, _g, _d in P.PERM_CATALOG]
    assert set(keys) == set(P.ALL_PERMISSIONS), "权限点与中文释义目录不同步"
    assert len(keys) == len(P.ALL_PERMISSIONS), "目录里有重复的权限点"


def test_every_perm_has_label_group_desc():
    for key, label, group, desc in P.PERM_CATALOG:
        assert label.strip(), f"{key} 缺中文名"
        assert group.strip(), f"{key} 缺分组"
        assert len(desc.strip()) >= 8, f"{key} 的释义太短，起不到说明作用"
        assert P.PERM_LABELS[key] == label


def test_groups_contain_each_perm_once():
    seen = [item["key"] for items in P.PERM_GROUPS.values() for item in items]
    assert sorted(seen) == sorted(P.ALL_PERMISSIONS)


# ------------------------------------------------------------ 默认矩阵
def test_default_matrix_equals_preset(db):
    P.load_overrides(db)
    matrix = P.effective_role_perms()
    for role in P.ROLE_PERMS:
        assert matrix[role] == P.ROLE_PERMS[role]
    assert not any(P.is_custom(r) for r in P.EDITABLE_ROLES)


def test_perms_for_without_loading_uses_defaults():
    # 没人调 load_overrides（脚本里直接 import）时不能炸
    assert P.perms_for(_U("sales")) == P.ROLE_PERMS["sales"]
    assert P.perms_for(None) == set()


# ------------------------------------------------------------ 自定义保存
def test_save_custom_matrix_takes_effect(db):
    P.load_overrides(db)
    matrix = {r: sorted(P.ROLE_PERMS[r]) for r in P.EDITABLE_ROLES}
    matrix["sales"] = [p for p in matrix["sales"] if p != "report:view"]

    after = P.save_matrix(db, matrix)

    assert "sales" in after
    assert "report:view" not in P.perms_for(_U("sales"))
    assert P.is_custom("sales")
    assert not P.is_custom("finance")
    # 其余角色回落到默认
    assert P.perms_for(_U("finance")) == P.ROLE_PERMS["finance"]


def test_custom_matrix_survives_reload(db):
    """模拟重启：重新从库里加载，自定义要还在。"""
    P.load_overrides(db)
    P.save_matrix(db, {"installer": ["task:view"]})
    P.reset_cache()  # 丢掉内存缓存，等于进程重启
    P.load_overrides(db)
    assert P.perms_for(_U("installer")) == {"task:view"}


def test_admin_cannot_be_downgraded(db):
    P.load_overrides(db)
    after = P.save_matrix(db, {"admin": [], "sales": sorted(P.ROLE_PERMS["sales"])})
    assert "admin" not in after
    assert P.perms_for(_U("admin")) == set(P.ALL_PERMISSIONS)


def test_unknown_perm_dropped(db):
    P.load_overrides(db)
    after = P.save_matrix(db, {"sales": ["customer:view", "hack:everything", 123]})
    assert after["sales"] == {"customer:view"}


def test_save_same_as_default_clears_override(db):
    P.load_overrides(db)
    P.save_matrix(db, {"sales": ["customer:view"]})
    assert P.is_custom("sales")

    P.save_matrix(db, {"sales": sorted(P.ROLE_PERMS["sales"])})
    assert not P.is_custom("sales")
    assert P.perms_for(_U("sales")) == P.ROLE_PERMS["sales"]


def test_reset_restores_defaults(db):
    P.load_overrides(db)
    P.save_matrix(db, {"sales": ["customer:view"]})
    P.reset_matrix(db)
    assert not P.is_custom("sales")
    assert P.perms_for(_U("sales")) == P.ROLE_PERMS["sales"]


def test_broken_json_in_db_is_ignored(db):
    from app.services import settings as settings_svc

    settings_svc.set_(db, P.PERM_MATRIX_KEY, "{not json at all")
    db.flush()
    assert P.load_overrides(db) == {}
    assert P.perms_for(_U("sales")) == P.ROLE_PERMS["sales"]
