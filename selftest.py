"""部署后自检（不依赖 pytest）：逐项打印 SUMMARY=xxxxxx。

用 docker cp 丢进容器跑：
    docker cp selftest.py hvaccrm:/tmp/selftest.py
    docker exec hvaccrm python3 /tmp/selftest.py
"""
from __future__ import annotations

import traceback

RES: dict[str, int] = {}


def chk(name: str):
    def deco(fn):
        try:
            fn()
            RES[name] = 1
        except Exception:  # noqa: BLE001
            RES[name] = 0
            print(f"FAIL {name}")
            traceback.print_exc()
        return fn

    return deco


@chk("import")
def _import():
    import app.main  # noqa: F401


@chk("catalog")
def _catalog():
    from app import permissions as P

    keys = [k for k, _l, _g, _d in P.PERM_CATALOG]
    assert set(keys) == set(P.ALL_PERMISSIONS), "目录与权限点不一致"
    assert len(keys) == len(P.ALL_PERMISSIONS), "目录有重复"
    seen = [i["key"] for items in P.PERM_GROUPS.values() for i in items]
    assert sorted(seen) == sorted(P.ALL_PERMISSIONS)
    for k, label, group, desc in P.PERM_CATALOG:
        assert label.strip() and group.strip() and len(desc.strip()) >= 8, k


@chk("default")
def _default():
    from app import permissions as P

    P.reset_cache()
    m = P.effective_role_perms()
    for role in P.ROLE_PERMS:
        assert m[role] == P.ROLE_PERMS[role], role


@chk("save")
def _save():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import permissions as P
    from app.models import Base

    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)
    db = S()
    try:
        P.reset_cache()
        P.load_overrides(db)

        matrix = {r: sorted(P.ROLE_PERMS[r]) for r in P.EDITABLE_ROLES}
        matrix["sales"] = [p for p in matrix["sales"] if p != "report:view"]
        after = P.save_matrix(db, matrix)
        assert "sales" in after, "自定义没写进缓存（flush 漏了？）"

        class U:
            role = "sales"

        assert "report:view" not in P.perms_for(U()), "保存后权限没生效"

        # 模拟重启：丢缓存重新加载
        P.reset_cache()
        P.load_overrides(db)
        assert "report:view" not in P.perms_for(U()), "重启后自定义丢了"

        # admin 不可降权
        after2 = P.save_matrix(db, {"admin": []})
        assert "admin" not in after2, "admin 被写进覆盖了"

        class A:
            role = "admin"

        assert P.perms_for(A()) == set(P.ALL_PERMISSIONS), "admin 被降权"

        # 存回默认 = 清空覆盖
        P.save_matrix(db, {r: sorted(P.ROLE_PERMS[r]) for r in P.EDITABLE_ROLES})
        assert not any(P.is_custom(r) for r in P.EDITABLE_ROLES), "存默认后仍标记为自定义"

        # 重置
        P.save_matrix(db, {"installer": ["task:view"]})
        P.reset_matrix(db)
        assert not P.is_custom("installer"), "重置没清掉覆盖"
    finally:
        db.close()
        eng.dispose()
        P.reset_cache()


@chk("template")
def _template():
    from pathlib import Path

    from app.templating import templates

    src = Path("app/templates/admin/permissions.html").read_text(encoding="utf-8")
    templates.env.parse(src)
    assert "perm-matrix" in src
    assert "matrix_json" in src


@chk("routes")
def _routes():
    from app.main import app as fastapi_app

    paths = set()
    for r in fastapi_app.routes:
        methods = getattr(r, "methods", None)
        if methods:
            for m in methods:
                paths.add((r.path, m))
    assert ("/admin/permissions", "POST") in paths, "保存路由没注册"
    assert ("/admin/permissions/reset", "POST") in paths, "重置路由没注册"
    assert ("/admin/permissions", "GET") in paths


if __name__ == "__main__":
    summary = "".join(str(RES[k]) for k in ("import", "catalog", "default", "save", "template", "routes"))
    print("SUMMARY=" + summary)
