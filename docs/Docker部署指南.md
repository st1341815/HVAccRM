# oneCRM · Docker 部署指南

> 本文覆盖 oneCRM 的 Docker 镜像构建、容器编排、配置、数据持久化、备份、升级、反向代理与排障。
> 面向有 Docker 基础的使用者；如果是飞牛（fnOS）NAS，可结合仓库根目录的 `DEPLOY_NAS.md` 一起看。

---

## 目录

1. [架构概览](#1-架构概览)
2. [前置条件](#2-前置条件)
3. [快速开始](#3-快速开始)
4. [配置说明（环境变量）](#4-配置说明环境变量)
5. [数据持久化与目录结构](#5-数据持久化与目录结构)
6. [镜像与容器详解](#6-镜像与容器详解)
7. [升级 / 更新](#7-升级--更新)
8. [备份与恢复](#8-备份与恢复)
9. [反向代理与 HTTPS](#9-反向代理与-https)
10. [健康检查与日志](#10-健康检查与日志)
11. [常见问题排查](#11-常见问题排查)
12. [安全清单](#12-安全清单)

---

## 1. 架构概览

```
┌────────────────────────────────────────────┐
│  宿主机（Docker Host）                        │
│                                             │
│  容器 crm  (镜像 crm:local, 非 root 1000:1000) │
│  ├─ uvicorn :8000  ←  唯一 HTTP 服务         │
│  ├─ /data   → 数据库 + 媒体 + 备份 + 日志     │
│  └─ /config → 部署配置（只读）                 │
│         │                                    │
│  端口映射 8090:8000                            │
└────────────────────────────────────────────┘
```

- 应用只监听**容器内 8000 端口**，通过 compose 的 `ports` 映射到宿主机 `8090`。
- 容器**不处理 HTTPS**，HTTPS 由宿主机反向代理（nginx / 飞牛反代 / Traefik 等）终结。
- 数据库为 **SQLite(WAL)**，单文件 `/data/crm.db`，无需外部数据库服务。

---

## 2. 前置条件

| 项 | 要求 |
| --- | --- |
| Docker | 20.10+ |
| Docker Compose | 插件版 `docker compose`（`docker-compose` 也行） |
| 磁盘 | 媒体照片会随业务增长，建议预留数 GB |
| 端口 | 宿主机 `8090` 可用（可改） |

---

## 3. 快速开始

```bash
# 1) 获取源码（git clone 或解压发布包）并进入目录
git clone https://github.com/st1341815/oneCRM.git
cd oneCRM

# 2) 准备运行配置（至少改 APP_SECRET_KEY 与 ADMIN_PASS）
cp .env.example .env
#    编辑 .env：APP_SECRET_KEY 用随机串，ADMIN_PASS 用强密码

# 3) 构建并启动
docker compose up -d --build

# 4) 查看状态与健康检查
docker compose ps
curl -s http://127.0.0.1:8090/health
# 期望：{"status":"ok","db":true,"version":"..."}

# 5) 浏览器打开 http://<宿主机IP>:8090 ，用 ADMIN_USER / ADMIN_PASS 登录
```

> **首次启动**会自动执行 `app.bootstrap`：迁移数据库、初始化 admin 账号、写入标准工序模板。之后重复启动不会重建。

---

## 4. 配置说明（环境变量）

所有配置通过环境变量注入，推荐在项目根目录的 `.env` 里维护（compose 会自动读取）。

### 必填

| 变量 | 说明 | 示例 |
| --- | --- | --- |
| `APP_SECRET_KEY` | 会话签名密钥，**必须**改成随机串 | `python3 -c "import secrets;print(secrets.token_urlsafe(48))"` |
| `ADMIN_PASS` | admin 初始密码（仅首次创建生效） | 强密码 |

### 账号与会话

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `ADMIN_USER` | `admin` | admin 登录账号 |
| `ADMIN_FORCE_PASSWORD_CHANGE` | `false` | `true` 则 admin 首次登录强制改密（生产建议 true） |
| `SESSION_MAX_AGE` | `43200`（12h） | 会话有效期（秒） |
| `SESSION_COOKIE` | `crm_session` | 会话 Cookie 名 |

### 品牌名

| 变量 | 默认 |
| --- | --- |
| `APP_NAME` | 暖通空调客户管理系统 |
| `APP_NAME_EN` | oneCRM |

### 路径（容器内，一般不用改）

| 变量 | 默认 |
| --- | --- |
| `DB_PATH` | `/data/crm.db` |
| `DATA_DIR` | `/data` |
| `CONFIG_DIR` | `/config` |

### 其他

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `TZ` | `Asia/Shanghai` | 时区 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `APP_PORT` | `8000` | 容器内监听端口（改它需同步改 `ports` 映射） |
| `THUMB_MAX_EDGE` | `1280` | 缩略图最长边（px） |
| `THUMB_QUALITY` | `80` | 缩略图 WebP 质量 |
| `BACKUP_KEEP` | `30` | 每日自动备份保留份数 |
| `LOGIN_MAX_ATTEMPTS` | `5` | 登录失败次数上限 |
| `LOGIN_LOCK_SECONDS` | `300` | 达到上限后的锁定秒数 |
| `WRITE_RETRY_ATTEMPTS` | `3` | SQLite 写锁重试次数 |

---

## 5. 数据持久化与目录结构

两个卷必须挂载（见 `docker-compose.yml`）：

| 挂载 | 容器内 | 说明 |
| --- | --- | --- |
| `./data` | `/data` | **可写**：数据库 + 媒体 + 备份 + 日志 |
| `./config` | `/config` | **只读**：部署配置 |

`/data` 内部结构：

```
data/
├── crm.db          # SQLite 主库（+ 运行时可能出现 -wal / -shm）
├── media/          # 照片原图 + 缩略图（按 客户ID/分类/ 组织）
├── backups/        # 每日自动备份 + 手动备份（.db 快照）
└── logs/           # crm.log 等应用日志
```

- 卷写在宿主机路径（`./data` 相对项目目录；NAS 上用 `docker-compose.nas.yml` 写绝对路径 `/vol1/1000/crm/data`）。
- **备份、迁移、升级只需保住 `data/` 目录**，容器/镜像可随时重建。

---

## 6. 镜像与容器详解

- **多阶段构建**（`Dockerfile`）：`builder` 阶段 `pip install --prefix` 只产依赖，`runtime` 阶段拷入依赖 + 源码，镜像更小。
- **非 root 运行**：容器内固定 `user: "1000:1000"`，与飞牛 NAS 的普通用户一致，避免权限问题。
- **入口**（`scripts/entrypoint.sh`）：`python -m app.bootstrap`（幂等：迁移 + admin + 工序模板）→ `uvicorn app.main:app`（`--workers 1`，SQLite 单进程写）。
- **资源限制**：`mem_limit: 512m`；日志 `json-file`，`max-size 5m / max-file 3`。
- **健康检查**：周期性请求 `/health`，返回 200 即 healthy。

构建/启动常用命令：

```bash
docker compose build                 # 仅构建镜像
docker compose up -d                 # 后台启动
docker compose up -d --build         # 重建并启动
docker compose logs -f crm           # 跟踪日志
docker compose restart crm           # 重启
docker compose down                  # 停止并删除容器（不删数据卷）
docker compose down -v               # ⚠️ 连同数据卷一起删（慎用）
```

---

## 7. 升级 / 更新

每次发布新版本时：

```bash
cd oneCRM
git pull                            # 拉最新代码
set -a; . .env; set +a              # 载入配置
docker compose up -d --build        # 重建镜像并滚动更新
docker compose ps                   # 确认 healthy
curl -s http://127.0.0.1:8090/health
```

- 入口脚本会自动跑 Alembic 迁移，**无需手动改库**。
- 升级前建议先做一次手动备份（见下）。

---

## 8. 备份与恢复

### 自动备份
- 应用内每日定时（`03:00`）自动备份数据库到 `data/backups/`，保留最近 `BACKUP_KEEP`（默认 30）份。

### 手动备份
- 登录系统 →「系统 → 备份 → 立即备份」。
- 或直接拷贝文件：

```bash
# 停写会变更安全，但 SQLite + WAL 下直接 cp 主库可能缺最新提交，
# 更稳妥：用应用内备份功能，或容器内 sqlite3 在线备份
docker compose exec crm sh -c 'cp /data/crm.db /data/backups/manual-$(date +%s).db'
```

> **重要**：备份数据库时，`media/` 里的照片也要一起备份（照片文件不在 db 里）。最省事的整体备份 = 打包整个 `data/` 目录。

### 恢复
```bash
docker compose down
# 用备份的 data/ 覆盖现有 data/
# （含 crm.db + media/ + backups/）
docker compose up -d
```

---

## 9. 反向代理与 HTTPS

容器只提供 HTTP，对外建议加一层 HTTPS 反代。

### nginx 示例

```nginx
server {
    listen 443 ssl;
    server_name crm.example.com;

    ssl_certificate     /etc/ssl/crm/fullchain.pem;
    ssl_certificate_key /etc/ssl/crm/privkey.pem;

    client_max_body_size 50m;          # 允许上传较大照片

    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 飞牛 NAS
在飞牛「Docker → 项目」或系统反向代理里把域名/端口指到 `8090`，并申请证书即可。

---

## 10. 健康检查与日志

- 健康端点：`GET /health` → `{"status":"ok","db":true,"version":"..."}`。
- 容器状态：`docker compose ps` 看 `STATUS` 是否 `Up (healthy)`。
- 应用日志：`docker compose logs -f crm`（容器 stdout）。
- 文件日志：`data/logs/crm.log`（应用内，含异常堆栈）。

---

## 11. 常见问题排查

| 现象 | 可能原因 / 处理 |
| --- | --- |
| 启动即退出 | `.env` 缺 `APP_SECRET_KEY`（compose 用 `:?` 强校验）；`docker compose logs crm` 看报错 |
| 无法访问 8090 | 端口被占用 / 防火墙；`docker compose ps` + `curl 127.0.0.1:8090/health` |
| 数据权限异常 | 挂载目录属主不是 UID 1000；`chown -R 1000:1000 data` |
| 数据库锁 / busy | 单实例设计，勿多副本共写同一 `crm.db`；写锁已内置重试 |
| 图片上传失败 | 检查 `data/media/` 是否可写、磁盘是否满 |
| 登录后掉线 | `APP_SECRET_KEY` 变化会导致旧会话失效；`SESSION_MAX_AGE` 到期 |

---

## 12. 安全清单

- [ ] `APP_SECRET_KEY` 改为随机强串，勿用默认值
- [ ] `ADMIN_PASS` 用强密码，`ADMIN_FORCE_PASSWORD_CHANGE=true`
- [ ] 对外走 HTTPS（反代 + 证书）
- [ ] 定期备份 `data/`（库 + 照片一起）
- [ ] 不要暴露 8000 直连；只映射 8090（或反代内网端口）
- [ ] 启用 TOTP 二步验证（系统内可开）
- [ ] 限制容器资源（已内置 `mem_limit`）

---

## 附：与开发部署文档的分工

| 文档 | 面向 |
| --- | --- |
| `docs/使用手册.md` | 业务使用者（怎么用） |
| `docs/Docker部署指南.md`（本文） | 部署/运维（怎么装怎么管） |
| `README.md` | 开发者（架构、实现、本地跑） |
| `DEPLOY_NAS.md` | 飞牛 NAS 特定踩坑记录 |
