#!/bin/sh
# 在 NAS 上构建 CRM 镜像（docker:cli 容器内执行，经挂载的 docker.sock 使用宿主 daemon）
# 安全约束：只同步代码目录/文件，绝不 rm -rf /work/crm，绝不触碰 data/ 与 config/
set -x
cd /work || exit 9
rm -f /work/BUILD_OK /work/BUILD_FAIL

{
  echo "=== build start $(date) ==="
  docker version --format '{{.Server.Version}}' || exit 1

  STAGE=/work/.crm-stage
  rm -rf "$STAGE"
  mkdir -p "$STAGE"
  tar xzf /work/crm-src.tar.gz -C "$STAGE" || { touch /work/FAIL_untar; exit 8; }

  mkdir -p /work/crm/data/media /work/crm/data/backups /work/crm/data/logs /work/crm/config
  for d in app migrations scripts docs; do
    rm -rf "/work/crm/$d"
    cp -a "$STAGE/crm/$d" "/work/crm/"
  done
  for f in Dockerfile alembic.ini pyproject.toml requirements.txt \
           docker-compose.yml docker-compose.nas.yml .env.example .gitignore README.md DEPLOY_NAS.md; do
    [ -f "$STAGE/crm/$f" ] && cp -a "$STAGE/crm/$f" "/work/crm/$f"
  done
  rm -rf "$STAGE"
  chown -R 1000:1000 /work/crm

  echo "--- code tree ---"
  ls -l /work/crm
  echo "--- data/config preserved ---"
  ls -ld /work/crm/data /work/crm/config
  ls -l /work/crm/data

  echo "--- docker build ---"
  cd /work/crm || exit 7
  docker build -t crm:local .
  echo "--- image ---"
  docker image ls crm
  echo "=== build done $(date) ==="
} > /work/build.log 2>&1

if grep -q "naming to .*crm:local" /work/build.log && grep -q "build done" /work/build.log; then
  touch /work/BUILD_OK
else
  touch /work/BUILD_FAIL
fi
