# ---------- 构建阶段：只负责产出依赖 ----------
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=60 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
# 只装运行依赖；测试依赖在 requirements-test.txt（不进生产镜像）
# 用清华源：直连 files.pythonhosted.org 会偶发 ReadTimeout（实测构建中途挂掉）
COPY requirements.txt .
RUN pip install --prefix=/install --no-warn-script-location --retries 8 --timeout 60 \
        -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

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

# 公众号草稿同步要在容器内自己跑浏览器（迁移时不能依赖宿主/Octop 环境）。
# 两个关键点，改之前先看：
#   1. Debian 13(trixie) 才有原生 chromium 包，这里装的是 156.x；
#   2. apt 必须带 -o APT::Key::UseSqv=false —— trixie 的 sqv 签名验证在无 tty
#      的容器里会永久挂住（实测卡 20 分钟无输出）。
# 换清华源否则下载慢到不可用。这一层放在 COPY 代码之前，改代码不会重装浏览器。
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources \
 && apt-get -o APT::Key::UseSqv=false update -qq \
 && DEBIAN_FRONTEND=noninteractive apt-get -o APT::Key::UseSqv=false install -y --no-install-recommends \
        chromium fonts-liberation \
 && rm -rf /var/lib/apt/lists/*

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
    CMD python3 -c "import sys,urllib.request;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4).status==200 else 1)"

# entrypoint：迁移数据库 + 初始化 admin/工序模板，然后启动 HTTP 服务
CMD ["sh", "/app/scripts/entrypoint.sh"]
