# NAS 部署与调试记录

设备：飞牛 OS（fnOS）· x86_64 · Docker 28.5.2 · 源码目录 `/vol1/1000/crm`

## 1. 当前部署状态（已验证）

| 项 | 值 |
| --- | --- |
| 访问地址 | `http://192.168.11.8:8090` |
| 容器名 / 镜像 | `crm` / `crm:local`（177 MB，多阶段构建，非 root） |
| 挂载 | `/vol1/1000/crm/data → /data`、`/vol1/1000/crm/config → /config:ro` |
| 环境变量 | `TZ=Asia/Shanghai`、`APP_SECRET_KEY`、`ADMIN_USER/ADMIN_PASS`、`DB_PATH=/data/crm.db`、`DATA_DIR=/data`、`CONFIG_DIR=/config`、`LOG_LEVEL=INFO` |
| 资源限制 | `mem_limit 512m`，日志 `json-file 5m × 3` |
| 健康检查 | 容器内 `GET /health`（60s 间隔）→ `healthy` |
| 验证结果 | `scripts/smoke_test.sh` 92/92 通过 |

部署变量保存在 NAS：`/vol1/1000/crm/config/deploy.env`（含 `APP_SECRET_KEY` 与 `ADMIN_PASS`，请自行改密后更新）。

## 2. 常用运维命令（NAS 上执行）

```bash
cd /vol1/1000/crm
set -a; . config/deploy.env; set +a

docker compose -f docker-compose.nas.yml up -d --build   # 构建并启动
docker compose -f docker-compose.nas.yml logs -f         # 看日志
docker compose -f docker-compose.nas.yml restart         # 重启
docker compose -f docker-compose.nas.yml down            # 停止（数据保留在 ./data）

tail -f data/logs/crm.log                                # 应用文件日志
ls -lt data/backups | head                               # 备份列表
```

容器内时间线：`entrypoint.sh` → `python -m app.bootstrap`（Alembic 迁移 + FTS5 + 9 工序 + admin）→ `uvicorn 0.0.0.0:8000`。

## 3. 无需 SSH 时的远程部署方式（本次使用）

NAS 上未开 SSH 时，用 `trim-cli`（fnOS CLI）完成上传、解压与镜像构建：

```bash
# 1) 上传源码压缩包并解压到 /vol1/1000（file extract 为异步任务，耐心等待）
trim-cli --profile home --allow-insecure-http file upload /vol1/1000 ./crm-src.tar.gz --overwrite replace --yes
trim-cli --profile home --allow-insecure-http file extract /vol1/1000/crm-src.tar.gz /vol1/1000 --overwrite replace --yes

# 2) 拉取基础镜像
trim-cli --profile home --allow-insecure-http docker image pull python:3.12-slim --yes
trim-cli --profile home --allow-insecure-http docker image pull docker:cli --yes

# 3) 用 docker:cli 容器（挂载宿主 docker.sock）构建镜像
trim-cli --profile home --allow-insecure-http docker container create \
  --image docker:cli --name crm-build \
  --mount /vol1/1000:/work:rw --mount /var/run/docker.sock:/var/run/docker.sock:rw \
  --cmd sh --cmd /work/nas_build.sh --start --yes

# 4) 启动应用容器
set -a; . /vol1/1000/crm/config/deploy.env; set +a
trim-cli --profile home --allow-insecure-http docker container create \
  --image crm:local --name crm \
  --mount /vol1/1000/crm/data:/data:rw --mount /vol1/1000/crm/config:/config:ro \
  --port 8090:8000 --memory 512 --restart --start --yes \
  --env TZ=Asia/Shanghai --env "APP_SECRET_KEY=$APP_SECRET_KEY" \
  --env ADMIN_USER=admin --env "ADMIN_PASS=$ADMIN_PASS" \
  --env DB_PATH=/data/crm.db --env DATA_DIR=/data --env CONFIG_DIR=/config
```

> `trim-cli docker container create --cmd` 会按空格拆分成数组，**不能直接传带空格的命令**；
> 因此把逻辑写成脚本（`scripts/nas_build.sh`、`scripts/nas_debug.sh`）再 `--cmd sh --cmd /path/script.sh`。

## 4. 调试经验（踩过的坑）

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 容器不停重启，`PermissionError: /app/app/__init__.py` | 源码 tar 继承本机 umask 700，镜像内文件非 root 不可读 | `chmod 644/755` 后重新打包；Dockerfile 增加 `RUN chmod -R a+rX /app` |
| `containerCreate failed with errno 52428803` | bind mount 源目录不存在（重建脚本 `rm -rf /work/crm` 误删了 `data/`、`config/`） | 构建脚本改为「只覆盖代码」，`mkdir -p` 保证挂载目录存在 |
| 容器 exit 2，健康检查 unhealthy | `LOG_LEVEL=INFO` 传给 uvicorn 的大写值被 click Choice 拒绝（大小写敏感） | 新增 `scripts/entrypoint.sh`，把级别转小写后再启动 |
| 应用文件日志只有一行 | Alembic `fileConfig()` 默认 `disable_existing_loggers=True`，关掉了应用 logger | `migrations/env.py` 改为 `disable_existing_loggers=False`，并在迁移后恢复 root 级别 |
| `file extract` 长时间无结果 | 该接口是异步任务（先解出 `.tar` 再解包） | 等待并轮询 `file ls`；或用 `docker:cli` 容器内 `tar` |
| 改密/改权限后仍是旧状态 | 路由里改的是中间件加载的 detached ORM 对象 | `app/auth.py::fresh_user()` 重新加载后再写入 |

NAS 侧无法读取容器 stdout 时，本次的做法是临时起一个 `python:3.12-slim` 静态文件服务容器
（`python3 -m http.server 8099 -d /work`，挂载 `/vol1/1000`）来读取 `data/logs/crm.log`、
`build.log` 等文件；**调试结束务必删除该容器**（它会把家目录暴露成无认证的 HTTP 目录）。

## 5. 数据安全

- `data/` 是唯一数据源：`crm.db`(+WAL) + `media/` + `backups/` + `logs/`。
- 重建镜像、重命名容器都不影响数据；只有删除 `data/` 才会丢数据，操作前先 `data/backups/` 里手动备份一次。
