<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/st1341815/HVAccRM@main/app/static/icons/icon-192.png" width="96" alt="HVAccRM">
</p>

# HVAccRM · 暖通空调客户管理系统

面向暖通空调（中央空调 / 地暖 / 暖气片 / 新风 / 净水 / 明装改造）**已成交客户全生命周期**的垂直管理系统。
以**房号**为核心索引，管楼盘房号、客户、合同与增项、收款退款、成本与利润、施工工序与现场照片，
并把完工照片一键变成可以发给客户的**案例分享页**。

单门店 / 小团队自托管：**一条命令跑起来，数据全在自己硬盘上**，不依赖任何云服务。

- 技术栈：Python 3.12 + FastAPI · SQLite(WAL) · HTMX + Jinja2 · APScheduler · PWA（可装到手机桌面）
- 容器：监听 `8000`（常见映射 `8090`），以 `uid/gid 1000` 非 root 运行，`linux/amd64`，约 185 MB
- 健康检查：`GET /health` → `{"status":"ok","db":true,"version":"x.y.z"}`

---

## 快速开始

```bash
mkdir -p ./hvaccrm/{data,config}

docker run -d --name hvaccrm --restart unless-stopped \
  -p 8090:8000 \
  -e TZ=Asia/Shanghai \
  -v "$PWD/hvaccrm/data:/data" \
  -v "$PWD/hvaccrm/config:/config" \
  13uncle/hvaccrm:latest

curl -s http://127.0.0.1:8090/health
```

浏览器打开 `http://<服务器IP>:8090`。

### 第一次登录

- 用户名：`admin`
- 密码：**首次启动随机生成**，写在 `data/first_boot.txt`

```bash
cat ./hvaccrm/data/first_boot.txt        # 里面是用户名和一次性密码
```

首次登录会强制改密码；改完即可删除该文件。想自己指定初始密码，加 `-e ADMIN_PASS='你的强密码'`（仅首次创建生效）。

### docker compose（推荐）

```yaml
services:
  hvaccrm:
    image: 13uncle/hvaccrm:1.7.0        # 生产建议钉版本，勿用 latest
    container_name: hvaccrm
    restart: unless-stopped
    ports: ["8090:8000"]
    environment:
      - TZ=Asia/Shanghai
      - APP_SECRET_KEY=${APP_SECRET_KEY:-}   # 留空=首启自动生成并写入 /config/.app_secret
      - ADMIN_PASS=${ADMIN_PASS:-}
      - COOKIE_SECURE=${COOKIE_SECURE:-auto}
    volumes:
      - ./data:/data
      - ./config:/config
    mem_limit: 512m
```

```bash
docker compose up -d
```

---

## 标签策略

| 标签 | 含义 |
| --- | --- |
| `1.7.0` | **固定版本**，生产建议用这个（升级时改 tag 即可） |
| `latest` | 最新稳定版，跟随最新 `X.Y.Z` |

只发布完整 `X.Y.Z` 版本号 + `latest`，不提供 `1.7` / `1` 这类浮动短标签——避免"看起来没变、实际变了"。

---

## 目录与数据

| 容器内路径 | 内容 |
| --- | --- |
| `/data/crm.db` | SQLite 主库（WAL 模式，同时会有 `-wal/-shm`） |
| `/data/media/` | 现场照片原图 + WebP 缩略图（按客户/分类归档） |
| `/data/backups/` | 备份 zip（数据库 + 媒体 + 配置，自包含） |
| `/data/logs/crm.log` | 应用日志（2 MB × 3 轮转） |
| `/config/.app_secret` | 自动生成的会话签名密钥（0600） |

> ⚠️ **`/config` 请按可写挂载**。若你坚持只读挂载，请显式设置 `APP_SECRET_KEY`，否则密钥无法持久化，
> 每次重启都会让所有人重新登录。
> 迁移备份：整个 `/data` 目录拷走即可；换机器后放回同路径启动就恢复。

---

## 常用环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `APP_SECRET_KEY` | 空（自动生成） | 会话签名密钥；多副本共享会话时必须显式填同一个值 |
| `ADMIN_USER` / `ADMIN_PASS` | `admin` / 空（随机） | 仅首次启动创建时生效 |
| `ADMIN_FORCE_PASSWORD_CHANGE` | `false` | `true` 则首登强制改密（公网建议开） |
| `COOKIE_SECURE` | `auto` | `auto` 判定为 https 才给 Cookie 加 `Secure`；也可 `1` / `0` 强开强关 |
| `HSTS_MAX_AGE` | `0`（关） | >0 时下发 HSTS，建议改在反代层统一加 |
| `API_DOCS` | `false` | 是否暴露 `/api/docs`、`/api/openapi.json` |
| `SESSION_MAX_AGE` | `43200` | 会话有效期（秒） |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_MAX_ATTEMPTS_ACCOUNT` / `LOGIN_LOCK_SECONDS` | `5` / `20` / `300` | 登录限速（IP+账号 / 账号维度 / 锁定秒） |
| `TZ` | `Asia/Shanghai` | 时区 |
| `BACKUP_KEEP` | `30` | 自动备份保留份数 |
| `DB_PATH` / `DATA_DIR` / `CONFIG_DIR` | `/data/crm.db` / `/data` / `/config` | 路径 |

完整清单与环境变量详解见 GitHub 的 [`docs/Docker部署指南.md`](https://github.com/st1341815/HVAccRM/blob/main/docs/Docker%E9%83%A8%E7%BD%B2%E6%8C%87%E5%8D%97.md)。

---

## 功能

- **楼盘 / 房号**：城市-区县、楼栋、单元、房号级联；房号占用状态；客户录入时可直接快捷新建楼盘
- **客户**：手机、微信、意向产品、渠道、等级、负责人；中文全文检索（FTS5），短词与手机号自动退化 LIKE
- **合同 / 增项**：合同号自动生成（`ONE` + 日期 + 顺数）、产品类型、纸质合同拍照归档、增项追加
- **收款 / 退款**：登记、凭证截图、按合同汇总已收/未收
- **成本 / 利润**：材料成本（挂供应商）、施工费用（挂安装师傅）、介绍费、物流、售后；毛利与毛利率按合同核算
- **施工工序**：上门勘测（客户级）→ 前期施工 → 后期施工 → 调试验收（合同级）；现场照片、完工备注、跳过需写原因
- **对外案例**：工序完工自动生成案例页，带水印外链 / 二维码 / 访问统计；匿名分享只露楼盘+楼栋，不露姓名手机
- **报表**：按月 / 按年 / 按客户三种口径的销售额、成本、毛利、毛利率、已收
- **备份与导入**：一键生成自包含 zip（库 + 媒体 + 配置），导入后自动重启恢复，替换前留底
- **权限**：5 角色（管理员/销售/设计师/安装工/财务）× 19 个权限点 × 3 种数据范围；**设计师与安装工看不到金额**
- **安全**：PBKDF2-SHA256 口令、可选 TOTP 二步验证、CSRF 防护、登录限速、会话版本吊销、审计日志
- **PWA**：可加到手机桌面，弱网/离线有兜底页

---

## 升级

```bash
docker pull 13uncle/hvaccrm:1.7.0      # 或改成新版本号
docker compose up -d                   # 重建容器
```

启动时自动执行数据库迁移，无需手工操作；建议升级前在后台「系统 → 备份」点一次「立即备份」。

---

## 边界（先说清楚，省得你踩坑）

- **SQLite 单写者**：适合单门店、并发 < 10 人的场景；不适合多门店共享一套实例或多租户 SaaS。
- **单实例**：会话密钥在 `/config`，多副本要共享同一个 `APP_SECRET_KEY`；但没有做多副本的会话/缓存同步。
- **只记录实际进度，不做计划排期**：施工模块刻意没有"计划开工/完工日期"和延期计算。
- **备份与主库同卷**：默认都落在 `/data`，请自行把备份 zip 定期拷到别处（或改备份目录到异地存储）。

---

## 链接

- 源码与文档：<https://github.com/st1341815/HVAccRM>
- 版本发布：<https://github.com/st1341815/HVAccRM/releases>
- 使用手册（业务侧）：[docs/使用手册.md](https://github.com/st1341815/HVAccRM/blob/main/docs/%E4%BD%BF%E7%94%A8%E6%89%8B%E5%86%8C.md)
- 部署指南（运维侧）：[docs/Docker部署指南.md](https://github.com/st1341815/HVAccRM/blob/main/docs/Docker%E9%83%A8%E7%BD%B2%E6%8C%87%E5%8D%97.md)
- 版本更新记录：<https://github.com/st1341815/HVAccRM/blob/main/docs/%E7%89%88%E6%9C%AC%E6%9B%B4%E6%96%B0%E8%AE%B0%E5%BD%95.md>

## License

[MIT](https://github.com/st1341815/HVAccRM/blob/main/LICENSE)
