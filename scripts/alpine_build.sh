#!/bin/sh
# 临时构建执行器：用 alpine 容器 + apk 装 docker CLI，代跑 compose_build.sh
# 背景：NAS 上 docker:cli / docker:27-cli 镜像拉取始终失败（alpine 能拉成功），
#       而构建必须有个带 docker CLI 的容器挂宿主 docker.sock。
# 不改任何业务逻辑，只为完成这一次 build；用完即删。
set -x
DIR=/vol1/1000/Docker/Hvaccrm
LOG=$DIR/alpine_build.log
exec > "$LOG" 2>&1
rm -f "$DIR"/marker_*.txt "$DIR"/health_*.txt

echo "===== START $(date -Iseconds) ====="
sed -i 's|dl-cdn.alpinelinux.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apk/repositories 2>/dev/null || true
apk add --no-cache docker-cli docker-cli-compose docker-cli-buildx
APK_EXIT=$?
echo "APK_EXIT=$APK_EXIT"
if [ "$APK_EXIT" -ne 0 ]; then
  echo "MARKER=APK_FAIL"
  : > "$DIR/marker_APK_FAIL.txt"
  exit 0
fi

docker version --format '{{.Server.Version}}' || {
  echo "MARKER=DOCKER_CLI_FAIL"
  : > "$DIR/marker_DOCKER_CLI_FAIL.txt"
  exit 0
}

# 交给原有的正式构建脚本（build + up + health 文件名通道）
sh "$DIR/compose_build.sh"
echo "===== END $(date -Iseconds) ====="
exit 0
