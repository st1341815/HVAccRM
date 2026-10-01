# ---------- 构建阶段：只负责产出依赖 ----------
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
COPY requirements.txt .
RUN pip install --prefix=/install --no-warn-script-location -r requirements.txt

# ---------- 运行阶段 ----------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    TZ=Asia/Shanghai \
    HOME=/tmp \
    DB_PATH=/data/crm.db \
    DATA_DIR=/data \
    CONFIG_DIR=/config

# sqlite3 已包含在官方 slim 镜像中；无额外系统依赖
COPY --from=builder /install /usr/local

WORKDIR /app
COPY alembic.ini pyproject.toml ./
COPY migrations ./migrations
COPY app ./app
COPY scripts ./scripts
COPY docs ./docs

# 归一到可读权限（构建上下文可能带有严格 umask），并准备数据目录
RUN chmod -R a+rX /app \
 && chmod a+x /app/scripts/*.sh \
 && mkdir -p /data/media /data/backups /data/logs /config \
 && chmod -R a+rwX /data

# 非 root 运行（NAS 上固定 UID/GID 1000）
USER 1000:1000

EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import sys,urllib.request;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4).status==200 else 1)"

# entrypoint：迁移数据库 + 初始化 admin/工序模板，然后启动 HTTP 服务
CMD ["sh", "/app/scripts/entrypoint.sh"]
