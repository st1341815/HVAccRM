# HVAccRM · 暖通空调客户管理系统

面向暖通空调（中央空调 / 地暖 / 暖气片 / 新风 / 净水 / 明装改造等）**已成交客户全生命周期**的垂直管理工具。
房号为核心索引、成本归集与利润核算、工序化交付（**只记录实际进度，不做计划排期**）。

技术栈（按开发文档锁定）：Python 3.12 + FastAPI · SQLite(WAL) · HTMX + Jinja2 ·
Session/Cookie + TOTP · APScheduler · SQLite FTS5 · 本地文件系统 · Docker Compose。

> 📖 **使用手册**（面向使用者：登录、各模块操作、角色权限、手机端、FAQ）：
> 见 [`docs/使用手册.md`](docs/使用手册.md) —— 日常使用请从这里开始。
> 🐳 **Docker 部署指南**（面向运维：镜像/编排/配置/备份/升级/反代/排障）：
> 见 [`docs/Docker部署指南.md`](docs/Docker部署指南.md)。
> 📦 **预构建镜像**（GHCR，免构建）：`ghcr.io/st1341815/hvaccrm`，用法见下文 §2.1。
> 本文档面向开发者，讲的是环境搭建、部署与实现细节。

---

## 1. 快速开始（本地开发）

```bash
cd crm
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # 至少修改 APP_SECRET_KEY / ADMIN_PASS

export APP_SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_urlsafe(48))")
export DB_PATH=$PWD/data/crm.db DATA_DIR=$PWD/data CONFIG_DIR=$PWD/config
export ADMIN_USER=admin ADMIN_PASS='你的初始密码'

.venv/bin/python -m app.bootstrap                 # 建目录 + 迁移 + 工序模板 + admin
.venv/bin/python -m uvicorn app.main:app --port 8000
# 打开 http://127.0.0.1:8000  →  /login
```

可选演示数据（楼盘 / 房号 / 客户 / 合同 / 收款，幂等）：

```bash
.venv/bin/python -m scripts.seed_demo        # 空库时写入；--force 追加
```

## 2. 部署

### 2.1 使用预构建镜像（docker pull，推荐）

镜像已发布到 GitHub Container Registry（GHCR），无需 clone 源码或本地构建：

| 标签 | 说明 |
| --- | --- |
| `ghcr.io/st1341815/hvaccrm:latest` | 最新正式版 |
| `ghcr.io/st1341815/hvaccrm:1.4.0` | 固定版本（可复现部署） |

```bash
docker pull ghcr.io/st1341815/hvaccrm:latest
```

> 若 GHCR 包未设为 Public（GitHub → Packages → Package settings → Danger Zone → Change visibility），拉取前需登录：
>
> ```bash
> echo "$GHCR_TOKEN" | docker login ghcr.io -u st1341815 --password-stdin
> ```
>
> `GHCR_TOKEN` 为具备 `read:packages` 权限的 GitHub PAT。

**docker run 最小示例：**

```bash
mkdir -p /srv/hvaccrm/{data,config}

export APP_SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_urlsafe(48))")
export ADMIN_USER=admin
export ADMIN_PASS='你的初始密码'

docker run -d --name hvaccrm --restart unless-stopped \
  --user 1000:1000 -p 8090:8000 \
  -e TZ=Asia/Shanghai \
  -e APP_SECRET_KEY="$APP_SECRET_KEY" \
  -e ADMIN_USER="$ADMIN_USER" -e ADMIN_PASS="$ADMIN_PASS" \
  -e DB_PATH=/data/crm.db -e DATA_DIR=/data -e CONFIG_DIR=/config \
  -v /srv/hvaccrm/data:/data \
  -v /srv/hvaccrm/config:/config:ro \
  ghcr.io/st1341815/hvaccrm:latest

curl -s http://127.0.0.1:8090/health      # {"status":"ok","db":true,...}
```

**docker compose 示例（需先准备 `.env`，否则会报 `required variable APP_SECRET_KEY is missing a value`）：**

```bash
cp .env.example .env        # 然后修改 APP_SECRET_KEY / ADMIN_PASS
# 或一键生成随机密钥与初始密码（推荐）：
bash scripts/gen_env.sh
```

```yaml
services:
  hvaccrm:
    image: ghcr.io/st1341815/hvaccrm:latest
    container_name: hvaccrm
    restart: unless-stopped
    user: "1000:1000"
    ports:
      - "8090:8000"
    environment:
      - TZ=Asia/Shanghai
      - APP_SECRET_KEY=${APP_SECRET_KEY:?required}
      - ADMIN_USER=${ADMIN_USER:-admin}
      - ADMIN_PASS=${ADMIN_PASS:?required}
      - DB_PATH=/data/crm.db
      - DATA_DIR=/data
      - CONFIG_DIR=/config
    volumes:
      - ./data:/data
      - ./config:/config:ro
```

- 首次启动自动执行 Alembic 迁移、创建 admin 与默认工序模板；业务数据全部落在挂载的 `data/`。
- 镜像基于 `python:3.12-slim` 多阶段构建，非 root（uid/gid 1000）运行，暴露 8000，健康检查 `/health`。
- 版本与源码 Release 对齐：<https://github.com/st1341815/HVAccRM/releases>。

### 2.2 从源码构建（NAS / docker compose）

```bash
# NAS 上，源码目录 /vol1/1000/Docker/Hvaccrm
cd /vol1/1000/Docker/Hvaccrm
set -a; . config/deploy.env; set +a          # APP_SECRET_KEY / ADMIN_PASS 等
docker compose -f docker-compose.nas.yml up -d --build
curl -s http://127.0.0.1:8090/health         # {"status":"ok","db":true,...}
```

- 容器内固定 `UID/GID 1000`，非 root 运行；仅监听 HTTP，HTTPS 由 NAS 反代。
- `data/`（DB + 媒体 + 备份 + 日志）与 `config/`（部署变量）由 NAS 卷挂载，重建镜像不丢数据。
- 内存上限 `mem_limit: 512m`；日志 `max-size 5m / max-file 3`。
- 若报 `required variable APP_SECRET_KEY is missing a value`：先 `bash scripts/gen_env.sh` 生成 `.env`，或按上面方式加载 `config/deploy.env`。

## 3. 目录结构

```
crm/
├── docker-compose.yml          # 通用（相对路径挂载）
├── docker-compose.nas.yml      # NAS 专用（绝对路径 /vol1/1000/Docker/Hvaccrm）
├── Dockerfile                  # 多阶段构建 → python:3.12-slim，非 root 运行
├── pyproject.toml / requirements.txt
├── alembic.ini + migrations/   # Alembic 迁移（0001_initial）
├── scripts/
│   ├── entrypoint.sh           # 容器入口：bootstrap → uvicorn
│   ├── gen_env.sh              # 生成 .env（随机 APP_SECRET_KEY / ADMIN_PASS）
│   ├── seed_demo.py            # 演示数据
│   ├── smoke_test.sh           # 92 项端到端自检（核心链路 + 权限）
│   ├── nas_build.sh            # 在 NAS 上构建镜像（docker.sock 方式）
│   └── nas_debug.sh            # NAS 侧诊断：把启动过程写入 /data/debug.log
├── app/
│   ├── main.py                 # FastAPI 入口 + 中间件 + 异常处理 + /health
│   ├── config.py               # pydantic-settings（环境变量）
│   ├── db.py                   # 引擎、WAL、外键、busy_timeout、写锁重试
│   ├── models.py               # ORM 模型 + FTS5 DDL + 9 个标准工序
│   ├── security.py             # PBKDF2 口令、TOTP(RFC6238)、签名 Cookie
│   ├── auth.py                 # 登录/登出/改密/二步验证
│   ├── permissions.py          # 权限点 + 角色 + 数据范围（双重校验）
│   ├── audit.py                # 审计日志
│   ├── bootstrap.py            # 启动引导（迁移/FTS/工序/admin）
│   ├── scheduler.py            # 逾期重算 / 每日备份
│   ├── templating.py           # Jinja2 + Flash
│   ├── services/               # finance / tasks / photos / search / backup
│   ├── routers/                # customers / projects / contracts / payments
│   │                           # / tasks / photos / admin / ui
│   ├── templates/              # 页面 + _fragments（HTMX 片段）
│   └── static/                 # app.css + 本地 htmx.min.js（无 CDN 依赖）
└── docs/                       # 开发文档文本提取
```

## 4. 核心业务规则（实现位置）

| 规则 | 实现 |
| --- | --- |
| 三数核对（合同额 / 应收计划 / 已收） | `services/finance.py::contract_summary` |
| 逾期自动重算（每日 00:10） | `scheduler.py::refresh_overdue_job` |
| 超额拦截（已收+本次 ≤ 总额+增项） | `services/finance.py::check_payment` |
| 增项走 change_orders 并同步追加应收 | `routers/contracts.py::add_change_order` |
| 客户建单自动生成「上门勘测」工序（1:1 客户） | `services/tasks.py::generate_survey_task` |
| 合同建单自动生成施工工序（1:1 合同，前期/后期施工/调试验收逐个推进） | `services/tasks.py::generate_contract_tasks` |
| 前置依赖（上节点未完成不可开工） | `services/tasks.py::start_task` |
| 跳过节点必须填原因 | `services/tasks.py::skip_task` |
| 照片路径/命名/缩略图/去重 | `services/photos.py` |
| 房号级联 + 同楼盘模糊搜索 | `routers/projects.py` + 客户表单 JS |
| 权限点 + 数据范围双重校验 | `permissions.py`（`require()` + `visible_customer_ids`） |
| 金额隔离（设计师/安装工） | `permissions.py::can_see_amount` + 模板判断 |
| 会话失效（session_version +1） | `routers/admin.py` / `auth.py` |
| 每日备份保留 30 份 | `services/backup.py` + `scheduler.py` |

## 5. 验证

```bash
# 端到端自检（默认打本地 8012，可用环境变量指向 NAS）
BASE_URL=http://192.168.11.8:8090 ADMIN_PW='<admin 密码>' bash scripts/smoke_test.sh
```

覆盖：认证、页面可达性、楼盘/房号级联与模糊搜索、客户 CRUD 与 FTS、
合同三期计划、三数核对、超额拦截、退款、增项+同步计划、工序前置依赖与跳过原因、
照片多图上传/缩略图/Hash 去重、5 个角色的权限拦截、金额隔离、数据范围、
会话失效、备份、审计日志。

## 6. 角色与权限

| 角色 | 权限点 | 数据范围 |
| --- | --- | --- |
| admin | 全部 | all |
| sales | 客户 CRUD、看合同/收款、工序、上传照片、报表 | self |
| designer | 看客户、看工序、上传照片（**不可见金额**） | shared |
| installer | 看工序、上传照片（**白名单**：仅被指派任务的客户） | self |
| finance | 看客户/合同、收款管理、退款、报表 | all |

## 7. 运维

- 备份：每天 03:00 使用 SQLite 在线备份 API 写入 `data/backups/`，保留最近 `BACKUP_KEEP`(30) 份；系统页可手动触发。
- 媒体：`data/media/{customer_id}/{kind}/{uuid}.jpg` + `_thumb.webp`，DB 只存相对路径，文件名不含客户名/房号。
- 日志：stdout + `data/logs/crm.log`（2MB × 3 轮转）；级别由 `LOG_LEVEL` 控制。
- 首次启动自动建库建表并创建 admin（`ADMIN_USER` / `ADMIN_PASS`，仅首次生效）。

## 8. 版本说明

当前版本 **v1.4.0**（2026-10-15，全站 UI 视觉改版）；预构建镜像 `ghcr.io/st1341815/hvaccrm:1.4.0` 与源码 Release 保持同版本。

HVAccRM 已于 2026-10-13 发布 **v1.0.0**，这是自 v0.9.0 起的首个正式版里程碑。相对 v0.14.x 主要变化：

- 新增自动化测试套件（`tests/`，pytest，19 个用例），覆盖财务口径 / 权限 / 报表聚合 / 工序锁客
- 修复 installer 白名单权限 `&=`（交集）应为 `|=`（并集）——installer 此前看不到自己被指派任务的客户
- `pyproject.toml` 与 `app/__init__.py` 版本号已同步（1.0.0），并新增 `scripts/bump_version.py` 防再漂移
- `admin_force_password_change` 默认 True（部署漏配 `.env` 也强制首登改密）
- 新增 `DEVELOPING.md` 开发指南

开发者请先读 [`DEVELOPING.md`](DEVELOPING.md)；完整变更见 [`docs/版本更新记录.md`](docs/版本更新记录.md)。
