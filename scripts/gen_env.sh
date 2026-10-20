#!/bin/sh
# 生成 docker compose 用的 .env（随机 APP_SECRET_KEY + 随机 ADMIN_PASS）
#
# 用法：
#   bash scripts/gen_env.sh                # 生成 ./.env（已存在则拒绝覆盖）
#   bash scripts/gen_env.sh --force        # 覆盖已有 .env
#   bash scripts/gen_env.sh --pass 我的密码  # 指定 admin 初始密码
set -e

FORCE=0
ADMIN_PASS_VALUE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --pass) ADMIN_PASS_VALUE="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

ENV_FILE="$(pwd)/.env"
if [ -f "$ENV_FILE" ] && [ "$FORCE" -ne 1 ]; then
  echo "已存在 $ENV_FILE，未覆盖。需要覆盖请加 --force" >&2
  exit 1
fi

gen_secret() {
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import secrets;print(secrets.token_urlsafe(48))'
  elif command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 48 | tr -d '\n'
  else
    head -c 48 /dev/urandom | base64 | tr -d '\n'
  fi
}

APP_SECRET_KEY=$(gen_secret)
if [ -z "$ADMIN_PASS_VALUE" ]; then
  if command -v python3 >/dev/null 2>&1; then
    ADMIN_PASS_VALUE=$(python3 -c 'import secrets,string; a=string.ascii_letters+string.digits; print("".join(secrets.choice(a) for _ in range(16)))')
  else
    ADMIN_PASS_VALUE=$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 16)
  fi
fi

cat > "$ENV_FILE" <<EOF
# 由 scripts/gen_env.sh 生成 — 含密钥，勿提交到 git
APP_NAME=暖通空调客户管理系统
APP_NAME_EN=HVAccRM
APP_SECRET_KEY=$APP_SECRET_KEY
ADMIN_USER=admin
ADMIN_PASS=$ADMIN_PASS_VALUE
ADMIN_FORCE_PASSWORD_CHANGE=true
DB_PATH=/data/crm.db
DATA_DIR=/data
CONFIG_DIR=/config
TZ=Asia/Shanghai
LOG_LEVEL=INFO
EOF

chmod 600 "$ENV_FILE" 2>/dev/null || true

echo "已生成 $ENV_FILE"
echo "  APP_SECRET_KEY : 已随机生成"
echo "  ADMIN_USER     : admin"
echo "  ADMIN_PASS     : $ADMIN_PASS_VALUE"
echo
echo "下一步：docker compose up -d --build（或 docker compose up -d，使用 GHCR 镜像时）"
echo "登录后建议在「账户」页修改初始密码。"
