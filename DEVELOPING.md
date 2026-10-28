# HVAccRM 开发指南（DEVELOPING）

面向维护者：本地开发、测试、版本发布、部署的标准流程。使用者文档见 `docs/使用手册.md`，部署文档见 `docs/Docker部署指南.md`。

## 1. 本地环境

```bash
cd crm
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-test.txt   # 运行依赖 + 测试依赖（pytest/httpx 不进生产镜像）
cp .env.example .env                        # 可选：默认值即可启动（admin / admin888）
```

> ⚠️ 沙箱环境有全局 `PYTHONPATH` 污染（`pydantic_core` 是坏的）。跑任何 Python 一律
> `PYTHONPATH= .venv/bin/python ...` 清空后再跑，避免 import 到坏库。

## 2. 跑测试

```bash
PYTHONPATH= .venv/bin/python -m pytest tests/ -v
```

- 测试用**内存 SQLite**（`sqlite://`），每函数独立 `Session`，不碰真实 `data/`。
- `autoflush=False` 与生产一致：测试中新建对象后需 `db.flush()` 才能拿到 `id`。
- 新增/改动业务逻辑时，**先补测试再改代码**（TDD 思路），核心口径集中在
  `tests/test_finance.py`、`tests/test_reports.py`、`tests/test_permissions.py`。

## 3. 版本号（两个文件，必须同步）

版本号同时存在于：
- `app/__init__.py` 的 `__version__`
- `pyproject.toml` 的 `[project].version`

**统一用脚本改，不要手改**：

```bash
python scripts/bump_version.py 1.0.1      # 同时改两处
python scripts/bump_version.py            # 一致性检查（部署前跑一次）
```

## 4. 发版三件套（每次发版必做）

1. **bump 版本**：`python scripts/bump_version.py <x.y.z>`
2. **写 CHANGELOG**：`docs/版本更新记录.md` 末尾追加一条（类型 feat/fix/style/perf/chore/docs + 一句话内容）
3. **git commit**：改完立刻提交，工作区不许留未提交改动

> 历史教训：v0.14.34~v0.14.59 期间「未 commit 直接 file upload 部署」导致内容丢失，
> 自那以后强制「改完即 commit」。

### 4.1 打标签与发 Release（命名规则固定，别自创）

```bash
python scripts/bump_version.py 1.7.1          # 版本号两处同步
git commit -am "fix(crm): …"
git tag -a v1.7.1 -m "v1.7.1 …"               # tag 只有 vX.Y.Z
git push origin main refs/tags/v1.7.1         # 打 tag 会自动触发 Docker Hub 发布 workflow
```

| 对象 | 规则 |
| --- | --- |
| git tag | `vX.Y.Z` |
| **GitHub Release 标题** | **`HVAccRM vX.Y.Z`**（前缀固定 `HVAccRM `，**不追加主题**） |
| Release 正文首行 | `## vX.Y.Z · 主题`（主题放正文，不放标题） |
| Docker 镜像 | `13uncle/hvaccrm:X.Y.Z` + `:latest`（Actions 自动发布） |
| 本地回滚 tag | `hvaccrm:pre-X.Y.Z` |

> 原因：Release 列表里标题要能一眼按版本号对齐；主题属于「这一版做了什么」，放正文更合适。
> 完整规则见 `docs/版本更新记录.md` §二。

## 5. 提交规范

- 前缀：`feat:` / `fix:` / `style:` / `perf:` / `chore:` / `docs:` / `test:`
- 范围：`(crm)`，如 `feat(crm): 报表新增「按客户查询利润」视图 v0.14.74`
- 一次提交只做一件事；部署脚本 `compose_build.sh` 的 tag 更新用独立 `chore:` 提交

## 6. 部署到 NAS（file upload + 重建镜像）

见 `docs/Docker部署指南.md` 与 `DEPLOY_NAS.md`。要点：

1. `file upload <远程目录> <本地文件>` 的 `<远程目录>` 是**纯目录**（不带文件名）
2. NAS 部署目录是 `<data-directory>/Docker/Hvaccrm`（大写 H 小写 m）
3. 部署前更新 `compose_build.sh` 里的回滚 tag 为「当前线上版本」
4. 增量重建用 docker:cli 容器跑 `compose_build.sh`，日志看 `build_result.log`
5. 验证：`GET /health` 版本号 + 容器 `healthy` + 关键页面冒烟

## 7. 目录约定

```
app/            # 应用代码（routers / services / templates / static / models 等）
tests/          # pytest 测试（conftest.py + test_*.py）
migrations/     # Alembic（0001 ~ 0014 …）
scripts/        # entrypoint / seed_demo / smoke_test / bump_version 等
docs/           # 使用手册、部署指南、版本更新记录
```
