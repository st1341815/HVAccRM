#!/bin/sh
# 容器入口：初始化数据库 → 启动 uvicorn（仅监听 HTTP，HTTPS 交给 NAS 反代）
set -e

LEVEL=$(echo "${LOG_LEVEL:-info}" | tr 'A-Z' 'a-z')
PORT="${APP_PORT:-8000}"

echo "[entrypoint] $(date) 初始化数据库与初始数据…"
python -m app.bootstrap

echo "[entrypoint] 启动 uvicorn：0.0.0.0:${PORT} (log-level=${LEVEL})"
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT}" \
  --workers 1 \
  --no-access-log \
  --log-level "${LEVEL}"
