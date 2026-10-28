# HVAccRM · 暖通空调客户管理系统

面向暖通空调（中央空调 / 地暖 / 暖气片 / 新风 / 净水 / 明装改造等）**已成交客户全生命周期**的垂直管理工具。
房号为核心索引、成本归集与利润核算、工序化交付（**只记录实际进度，不做计划排期**）。

技术栈：Python 3.12 + FastAPI · SQLite(WAL) · HTMX + Jinja2 · Session/Cookie + TOTP ·
APScheduler · SQLite FTS5 · 本地文件系统 · Docker Compose · PWA（Service Worker 离线兜底）。

功能模块：楼盘/房号 · 客户 · 合同/增项 · 收款/退款 · 成本/供应商/利润 · 施工工序/现场照片 ·
对外案例/分享外链/分享统计 · 报表（按月/年/客户） · 全功能备份与导入 · 用户/角色/权限 · 审计日志。

> 🏠 **本仓库只放代码与通用文档**：具体部署地址（域名 / 内网 IP / 服务器路径）属于运维信息，
> 请记录在你的私有部署笔记里，不要提交回本仓库。
> 📖 **使用手册**（面向使用者：登录、各模块操作、角色权限、手机端、FAQ）：
> 见 [`docs/使用手册.md`](docs/使用手册.md) —— 日常使用请从这里开始。
> 🐳 **Docker 部署指南**（面向运维：镜像/编排/配置/备份/升级/反代/排障）：
> 见 [`docs/Docker部署指南.md`](docs/Docker部署指南.md)。
> 📦 **预构建镜像**（Docker Hub `13uncle/hvaccrm` / GHCR `ghcr.io/st1341815/hvaccrm`，免构建），用法见下文 §2.1。
> 📱 **微信小程序案例分享方案**（把案例同步到小程序、用微信卡片发给客户）：
> 见 [`docs/微信小程序案例同步方案.md`](docs/微信小程序案例同步方案.md)。
> 本文档面向开发者，讲的是环境搭建、部署与实现细节。

---

## 1. 快速开始（本地开发）

```bash
cd crm
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# 可选：cp .env.example .env 自定义密钥/密码；不复制也能用默认值启动

export DB_PATH=$PWD/data/crm.db DATA_DIR=$PWD/data CONFIG_DIR=$PWD/config
# 管理员：用户名 admin；密码在首次启动时生成，见 data/first_boot.txt（或显式设 ADMIN_PASS）

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

镜像已发布到 **GitHub Container Registry（GHCR）** 与 **Docker Hub**，无需 clone 源码或本地构建：

| 镜像源 | 标签 | 说明 |
| --- | --- | --- |
| **Docker Hub** | `13uncle/hvaccrm:latest` / `13uncle/hvaccrm:1.4.7` | 推荐（公开仓库，匿名可拉；每次打 `v*` tag 由 Actions 自动发布） |
| GHCR | `ghcr.io/st1341815/hvaccrm:latest` / `:1.4.1` | GitHub 容器仓库（**无自动化流程，可能滞后**；以 Docker Hub 为准） |

```bash
docker pull 13uncle/hvaccrm:latest            # Docker Hub
# 或
docker pull ghcr.io/st1341815/hvaccrm:latest  # GHCR
```

> 若 GHCR 包未设为 Public（GitHub → Packages → Package settings → Danger Zone → Change visibility），拉取前需登录：
>
> ```bash
> echo "$GHCR_TOKEN" | docker login ghcr.io -u st1341815 --password-stdin
> ```
>
> `GHCR_TOKEN` 为具备 `read:packages` 权限的 GitHub PAT。

**docker run 最小示例（默认值即可启动）：**

```bash
mkdir -p /srv/hvaccrm/{data,config}

docker run -d --name hvaccrm --restart unless-stopped \
  --user 1000:1000 -p 8090:8000 \
  -e TZ=Asia/Shanghai \
  -e ADMIN_USER=admin \
  -e DB_PATH=/data/crm.db -e DATA_DIR=/data -e CONFIG_DIR=/config \
  -v /srv/hvaccrm/data:/data \
  -v /srv/hvaccrm/config:/config:ro \
  13uncle/hvaccrm:latest

curl -s http://127.0.0.1:8090/health      # {"status":"ok","db":true,...}
```

**docker compose 示例（默认值即可启动，无需先建 `.env`）：**

```yaml
services:
  hvaccrm:
    image: 13uncle/hvaccrm:latest
    container_name: hvaccrm
    restart: unless-stopped
    user: "1000:1000"
    ports:
      - "8090:8000"
    environment:
      - TZ=Asia/Shanghai
      - APP_SECRET_KEY=${APP_SECRET_KEY:-}
      - ADMIN_USER=${ADMIN_USER:-admin}
      - ADMIN_PASS=${ADMIN_PASS:-}
      - ADMIN_FORCE_PASSWORD_CHANGE=${ADMIN_FORCE_PASSWORD_CHANGE:-false}
      - COOKIE_SECURE=${COOKIE_SECURE:-auto}
      - DB_PATH=/data/crm.db
      - DATA_DIR=/data
      - CONFIG_DIR=/config
    volumes:
      - ./data:/data
      - ./config:/config:ro
```

- **首次启动的 admin 密码**：未显式设置 `ADMIN_PASS` 时随机生成，写入 `data/first_boot.txt`（0600），首次登录强制改密；`APP_SECRET_KEY` 未设置时同样自动生成并保存到 `config/.app_secret`（0600）。
- 首次启动自动执行 Alembic 迁移、创建 admin 与默认工序模板；业务数据全部落在挂载的 `data/`。
- 镜像基于 `python:3.12-slim` 多阶段构建，非 root（uid/gid 1000）运行，暴露 8000，健康检查 `/health`。
- 版本与源码 Release 对齐：<https://github.com/st1341815/HVAccRM/releases>。
- **公网 / 生产环境**：建议显式设置 `APP_SECRET_KEY`（随机串）与 `ADMIN_PASS`，并建议 `ADMIN_FORCE_PASSWORD_CHANGE=true`；`COOKIE_SECURE=auto` 会在反代透传 `X-Forwarded-Proto: https` 时自动给 Cookie 加 `Secure`。

### 2.2 从源码构建（NAS / docker compose）

```bash
# NAS 上，源码目录按你的实际部署路径（示例：<data-directory>/Docker/Hvaccrm）
cd <data-directory>/Docker/Hvaccrm
set -a; . config/deploy.env; set +a          # APP_SECRET_KEY / ADMIN_PASS 等
docker compose -f docker-compose.nas.yml up -d --build
curl -s http://127.0.0.1:8090/health         # {"status":"ok","db":true,...}
```

- 容器内固定 `UID/GID 1000`，非 root 运行；仅监听 HTTP，HTTPS 由 NAS 反代。
- `data/`（DB + 媒体 + 备份 + 日志）与 `config/`（部署变量）由 NAS 卷挂载，重建镜像不丢数据。
- 内存上限 `mem_limit: 512m`；日志 `max-size 5m / max-file 3`。
- 默认值即可直接启动；如需自定义密钥/密码，按上面方式加载 `config/deploy.env`（或在同目录建 `.env`）。

## 3. 目录结构

```
crm/
├── docker-compose.yml          # 通用（相对路径挂载）
├── docker-compose.nas.yml      # NAS 专用（绝对路径 <data-directory>/Docker/Hvaccrm）
├── Dockerfile                  # 多阶段构建 → python:3.12-slim，非 root 运行
├── pyproject.toml / requirements.txt
├── alembic.ini + migrations/   # Alembic 迁移（0001 ~ 0015）
├── scripts/
│   ├── entrypoint.sh           # 容器入口：bootstrap → uvicorn
│   ├── bump_version.py         # 同步 app/__init__.py 与 pyproject.toml 版本号
│   ├── seed_demo.py            # 演示数据
│   ├── smoke_test.sh           # 端到端自检（核心链路 + 权限）
│   ├── normalize_project_names.py  # 楼盘名期数规范化（一次性脚本）
│   ├── rethumb.py              # 重建历史缩略图
│   ├── nas_build.sh            # 在 NAS 上构建镜像（docker.sock 方式）
│   └── nas_debug.sh            # NAS 侧诊断：把启动过程写入 /data/debug.log
├── tests/                      # pytest 套件（财务口径 / 权限 / 报表 / 备份 / 工序）
├── app/
│   ├── main.py                 # FastAPI 入口 + 中间件 + 异常处理 + /health
│   ├── config.py               # pydantic-settings（环境变量）
│   ├── db.py                   # 引擎、WAL、外键、busy_timeout、写锁重试
│   ├── models.py               # ORM 模型 + FTS5 DDL + 4 个标准工序
│   ├── security.py             # PBKDF2 口令、TOTP(RFC6238)、签名 Cookie
│   ├── auth.py                 # 登录/登出/改密/二步验证
│   ├── permissions.py          # 权限点 + 角色 + 数据范围（双重校验）
│   ├── audit.py                # 审计日志
│   ├── regions.py              # 安徽省城市/区县白名单
│   ├── bootstrap.py            # 启动引导（迁移/FTS/工序/admin）
│   ├── scheduler.py            # 逾期重算 / 每日 + 每周备份
│   ├── templating.py           # Jinja2 + Flash
│   ├── utils.py                # 站内 return_path 等通用工具
│   ├── services/               # finance / tasks / photos / search / backup
│   │                           # / cases / costs / numbering / settings
│   ├── routers/                # customers / projects / contracts / payments
│   │                           # / tasks / photos / cases / costs / reports
│   │                           # / admin / ui
│   ├── templates/              # 页面 + _fragments（HTMX 片段）
│   └── static/                 # app.css · htmx.min.js · PWA（manifest / SW / offline / icons）
└── docs/                       # 使用手册、Docker 部署指南、微信小程序方案、版本更新记录
```

## 4. 核心业务规则（实现位置）

| 规则 | 实现 |
| --- | --- |
| 合同额（减优惠）+ 增项 vs 已收（未收 = 总额 − 已收） | `services/finance.py::contract_summary` |
| 逾期重算（每日 00:10，按应收计划日期） | `scheduler.py::refresh_overdue_job` |
| 超额拦截（已收 + 本次 ≤ 总额 + 增项） | `services/finance.py::check_payment` |
| 增项写 `change_orders`，可选同步追加一期应收 | `routers/contracts.py::add_change_order` |
| 客户建单自动生成「上门勘测」工序（1:1 客户） | `services/tasks.py::generate_survey_task` |
| 合同建单自动生成施工工序（1:1 合同，前期/后期/调试验收逐个推进） | `services/tasks.py::generate_contract_tasks` |
| 前置依赖（上一节点未完成不可开工） | `services/tasks.py::start_task` |
| 跳过节点必须填原因 | `services/tasks.py::skip_task` |
| 完工自动建案例（前期/后期施工）并导入现场照片 | `services/tasks.py::finish_task` + `services/cases.py` |
| 客户锁定（前期施工完工自动锁定，新建合同过滤） | `services/tasks.py::finish_task` + `routers/contracts.py` |
| 案例外链（有效期 / 访问次数 / 手动失效） | `services/cases.py::share_state` + `routers/cases.py` |
| 报表口径（销售额/成本/已收各按自己的日期归集） | `routers/reports.py` |
| 照片路径/命名/缩略图/去重 | `services/photos.py` |
| 房号级联 + 同楼盘模糊搜索 | `routers/projects.py` + 客户表单 JS |
| 权限点 + 数据范围双重校验 | `permissions.py`（`require()` + `visible_customer_ids`） |
| 金额隔离（设计师/安装工） | `permissions.py::can_see_amount` + 模板判断 |
| 会话失效（session_version +1） | `routers/admin.py` / `auth.py` |
| 全功能备份 / 导入（db + media + config zip） | `services/backup.py` + `scheduler.py` |

## 5. 验证

```bash
# 端到端自检（默认打本地 8012，可用环境变量指向 NAS）
BASE_URL=http://127.0.0.1:8090 ADMIN_PW='<admin 密码>' bash scripts/smoke_test.sh
```

覆盖：认证、页面可达性、楼盘/房号级联与模糊搜索、客户 CRUD 与 FTS、
合同金额/增项、超额拦截、退款、工序前置依赖与跳过原因、完工锁客、
照片多图上传/缩略图/Hash 去重、5 个角色的权限拦截、金额隔离、数据范围、
会话失效、备份、审计日志。

## 6. 角色与权限

| 角色 | 权限点 | 数据范围 |
| --- | --- | --- |
| admin | 全部 | all |
| sales | 客户 CRUD、看合同/收款、成本录入、工序、上传照片、案例、报表 | self |
| designer | 看客户、看工序、上传照片、案例（**不可见金额**） | shared |
| installer | 看工序、上传照片、案例（**白名单**：仅被指派任务的客户） | self |
| finance | 看客户/合同、收款管理、退款、成本、案例、报表 | all |

## 7. 运维

- 备份：**默认每日 03:00 + 每周一 04:00**，用 SQLite 在线备份 API 生成 `crm-backup-*.zip`（db + media + config）写入 `data/backups/`；路径 / 频率 / 保留份数（默认 30）可在后台「备份」页修改，改完即时重排；支持手动备份、下载与导入恢复（导入后进程自动重启生效）。
- 媒体：`data/media/{customer_id}/{kind}/{uuid}.jpg` + `_thumb.webp`，DB 只存相对路径，文件名不含客户名/房号。
- 日志：stdout + `data/logs/crm.log`（2MB × 3 轮转）；级别由 `LOG_LEVEL` 控制。
- 首次启动自动建库建表并创建 admin（`ADMIN_USER` / `ADMIN_PASS`，仅首次生效）。

## 8. 版本说明

当前版本 **v1.7.0**（2026-10-28，安全加固版：登录开放重定向 / CSRF / 异常脱敏 / 密钥与初始口令随机化 / Cookie Secure / 依赖升级 / CI 门禁）；预构建镜像 **Docker Hub** `13uncle/hvaccrm:1.7.0`（含 `:latest`），与源码 Release 同版本。

**发版命名约定**：git tag `vX.Y.Z`；GitHub Release 标题固定 `HVAccRM vX.Y.Z`（主题只写在正文）；Docker 镜像 `<用户>/hvaccrm:X.Y.Z` + `:latest`；本地回滚 tag `hvaccrm:pre-X.Y.Z`。完整规则见 [`docs/版本更新记录.md`](docs/版本更新记录.md) §二。

> 更早的版本说明见 `docs/版本更新记录.md`；`v1.4.1`（2026-10-20）为「简化部署：默认密码 `admin888`、`APP_SECRET_KEY` 不再必填」。

HVAccRM 已于 2026-10-13 发布 **v1.0.0**，这是自 v0.9.0 起的首个正式版里程碑。相对 v0.14.x 主要变化：

- 新增自动化测试套件（`tests/`，pytest，19 个用例），覆盖财务口径 / 权限 / 报表聚合 / 工序锁客
- 修复 installer 白名单权限 `&=`（交集）应为 `|=`（并集）——installer 此前看不到自己被指派任务的客户
- `pyproject.toml` 与 `app/__init__.py` 版本号已同步（1.0.0），并新增 `scripts/bump_version.py` 防再漂移
- `admin_force_password_change` 默认 True（部署漏配 `.env` 也强制首登改密）
- 新增 `DEVELOPING.md` 开发指南

开发者请先读 [`DEVELOPING.md`](DEVELOPING.md)；完整变更见 [`docs/版本更新记录.md`](docs/版本更新记录.md)。
