#!/bin/sh
# HVAccRM 增量重建脚本（部署辅助，<不是业务代码>）
#
# 用法：上传到 /vol1/1000/Docker/Hvaccrm/ 后，用 docker:cli 容器执行：
#   docker container create --image docker:cli --name <name> \
#     --mount /var/run/docker.sock:/var/run/docker.sock:rw \
#     --mount /vol1/1000:/vol1/1000:rw \
#     --cmd sh --cmd /vol1/1000/Docker/Hvaccrm/compose_build.sh --start --yes
# 然后读 /vol1/1000/Docker/Hvaccrm/build_result.log（或另起 http server 读）。
#
# ⚠️ 每次部署前：把下面这行的 tag 更新为「当前线上版本号」，
#    以便新版本出问题时能 docker image tag <tag> hvaccrm:local 一键回滚。
cd /vol1/1000/Docker/Hvaccrm || exit 1
exec > /vol1/1000/Docker/Hvaccrm/build_result.log 2>&1

echo "===== START BUILD $(date -Iseconds) ====="
docker image tag hvaccrm:local hvaccrm:pre-1.0.0

echo "===== docker compose build ====="
docker compose -f docker-compose.yml -p hvaccrm build 2>&1
BUILD_EXIT=$?
echo "BUILD_EXIT=$BUILD_EXIT"
if [ "$BUILD_EXIT" -ne 0 ]; then
  echo "MARKER=BUILD_FAIL"
  exit 0
fi

echo "===== docker compose up -d ====="
docker compose -f docker-compose.yml -p hvaccrm up -d 2>&1
UP_EXIT=$?
echo "UP_EXIT=$UP_EXIT"
if [ "$UP_EXIT" -ne 0 ]; then
  echo "MARKER=UP_FAIL"
  exit 0
fi

echo "===== final state ====="
docker image ls hvaccrm 2>&1 | head -6 || true
docker container ls 2>&1 | head -6 || true
echo "MARKER=BUILD_OK"
echo "===== END BUILD $(date -Iseconds) ====="
exit 0
