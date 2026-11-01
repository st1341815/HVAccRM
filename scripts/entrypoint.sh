#!/bin/sh
# 容器入口：初始化数据库 → 启动 uvicorn（仅监听 HTTP，HTTPS 交给 NAS 反代）
set -e

LEVEL=$(echo "${LOG_LEVEL:-info}" | tr 'A-Z' 'a-z')
PORT="${APP_PORT:-8000}"

echo "[entrypoint] $(date) 初始化数据库与初始数据…"
python -m app.bootstrap

echo "[entrypoint] 启动 uvicorn：0.0.0.0:${PORT} (log-level=${LEVEL})"
# 访问日志保持开启：2026-11-02 排查短信发码问题时，因 --no-access-log
# 无法看到请求是否到达、返回什么状态码，定位成本极高。
# 可用 ACCESS_LOG=0 临时关闭。
if [ "${ACCESS_LOG:-1}" = "0" ]; then
  ACCESS_FLAG="--no-access-log"
else
  ACCESS_FLAG="--access-log"
fi
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT}" \
  --workers 1 \
  ${ACCESS_FLAG} \
  --log-level "${LEVEL}"
