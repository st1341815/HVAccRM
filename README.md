# 家居建材客户管理系统（CRM）

面向家居建材（定制橱柜 / 衣柜 / 门窗等）**已成交客户全生命周期**的垂直管理工具。
房号为核心索引、款项分期管理、工序化交付。

技术栈（按开发文档锁定）：Python 3.12 + FastAPI · SQLite(WAL) · HTMX + Jinja2 ·
Session/Cookie + TOTP · APScheduler · SQLite FTS5 · 本地文件系统 · Docker Compose。

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

## 2. 部署到 NAS（docker compose）

```bash
# NAS 上，源码目录 /vol1/1000/crm
cd /vol1/1000/crm
set -a; . config/deploy.env; set +a          # APP_SECRET_KEY / ADMIN_PASS 等
docker compose -f docker-compose.nas.yml up -d --build
curl -s http://127.0.0.1:8090/health         # {"status":"ok","db":true,...}
```

- 容器内固定 `UID/GID 1000`，非 root 运行；仅监听 HTTP，HTTPS 由 NAS 反代。
- `data/`（DB + 媒体 + 备份 + 日志）与 `config/`（部署变量）由 NAS 卷挂载，重建镜像不丢数据。
- 内存上限 `mem_limit: 512m`；日志 `max-size 5m / max-file 3`。

## 3. 目录结构

```
crm/
├── docker-compose.yml          # 通用（相对路径挂载）
├── docker-compose.nas.yml      # NAS 专用（绝对路径 /vol1/1000/crm）
├── Dockerfile                  # 多阶段构建 → python:3.12-slim，非 root 运行
├── pyproject.toml / requirements.txt
├── alembic.ini + migrations/   # Alembic 迁移（0001_initial）
├── scripts/
│   ├── entrypoint.sh           # 容器入口：bootstrap → uvicorn
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
│   ├── scheduler.py            # 逾期重算 / 延期扫描 / 每日备份
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
| 客户建单自动生成 9 个工序任务 | `services/tasks.py::generate_tasks` |
| 前置依赖（上节点未完成不可开工） | `services/tasks.py::start_task` |
| 跳过节点必须填原因 | `services/tasks.py::skip_task` |
| 延期预警（planned_end < 今日） | `services/tasks.py::is_delayed` + 首页红字 |
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
