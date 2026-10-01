#!/bin/sh
# 诊断脚本：在 crm:local 镜像内运行，把启动过程写入 /data/debug.log（可通过 filehost 读取）
{
  echo "=== debug run $(date) ==="
  id
  echo "--- mounts ---"
  ls -ld /data /config /app
  echo "--- env ---"
  env | grep -E "DB_PATH|DATA_DIR|CONFIG_DIR|ADMIN_USER|TZ|PYTHONPATH" | sed 's/APP_SECRET_KEY=.*/APP_SECRET_KEY=***/'
  echo "--- write test /data ---"
  touch /data/_writetest && echo "write ok" && rm -f /data/_writetest || echo "WRITE FAILED"
  echo "--- bootstrap ---"
  cd /app && python -m app.bootstrap
  echo "bootstrap exit=$?"
  echo "--- import app.main ---"
  python -c "import app.main; print('import ok')"
  echo "import exit=$?"
  echo "--- uvicorn quickstart 3s ---"
  timeout 5 python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level info
  echo "uvicorn exit=$?"
  echo "=== debug end ==="
} > /data/debug.log 2>&1
cat /data/debug.log
