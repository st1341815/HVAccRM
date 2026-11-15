#!/bin/sh
# NAS 侧「拉预构建镜像」部署脚本（不本地 build）
#
# 前提：vX.Y.Z 已通过 git tag 触发 GitHub Actions，镜像已推到 Docker Hub
#       `13uncle/hvaccrm:X.Y.Z`（见 .github/workflows/dockerhub.yml）。
# 用途：NAS 上历史镜像被清空、或不想在 NAS 上跑 build（省 CPU/内存/时间）时，
#       直接拉 Docker Hub 上由 CI 构建好的同一份镜像。
#
# 执行方式（NAS 无 SSH 时用 alpine 当 docker CLI 执行器，见 DEPLOY_NAS.md §3.1）：
#   trim-cli ... docker container create --image alpine:3.20 --name crm-pull \
#     --mount /var/run/docker.sock:/var/run/docker.sock:rw \
#     --mount /vol1/1000:/vol1/1000:rw \
#     --cmd sh --cmd /vol1/1000/Docker/Hvaccrm/nas_pull_deploy.sh --cmd 1.12.10 --start --yes
#
# 注意：`docker container create --cmd` 按空格拆分参数，所以 --cmd 后面每个片段都要单列。
set -x
VER=$1
DIR=/vol1/1000/Docker/Hvaccrm
LOG=$DIR/pull_deploy.log
IMG=13uncle/hvaccrm:$VER

exec > "$LOG" 2>&1
rm -f "$DIR"/pullmark_*.txt "$DIR"/health_*.txt
echo "===== START $(date -Iseconds) VER=$VER ====="

if [ -z "$VER" ]; then
  echo "MARKER=NO_VERSION"
  : > "$DIR/pullmark_NO_VERSION.txt"
  exit 0
fi

# 0) apk 装 docker CLI（alpine 执行器内需要；换清华源否则卡死）
if ! command -v docker >/dev/null 2>&1; then
  sed -i 's|dl-cdn.alpinelinux.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apk/repositories 2>/dev/null || true
  apk add --no-cache docker-cli docker-cli-compose docker-cli-buildx \
    || { echo "MARKER=APK_FAIL"; : > "$DIR/pullmark_APK_FAIL.txt"; exit 0; }
fi

# 1) 先把当前线上镜像留作回滚点。
#    ⚠️ `pre-X.Y.Z` 的语义是「升级到 X.Y.Z 之前的镜像」，本地 build 流程可能已经写过同名 tag
#    并指向上一版（如 pre-1.12.10 = v1.12.9）。这里绝不覆盖它，否则回滚点会被顶成「新镜像」。
BK="hvaccrm:pre-$VER"
if docker image inspect "$BK" >/dev/null 2>&1; then
  BK="hvaccrm:pre-$VER-localbuild"
fi
docker image tag hvaccrm:local "$BK" 2>&1 || echo "no current image to tag"

# 2) 拉 CI 产出的镜像
docker image pull "$IMG" 2>&1
PULL_EXIT=$?
echo "PULL_EXIT=$PULL_EXIT"
if [ "$PULL_EXIT" -ne 0 ]; then
  echo "MARKER=PULL_FAIL"
  : > "$DIR/pullmark_PULL_FAIL.txt"
  exit 0
fi

# 3) 换成本地名，按原 compose 项目重建容器（--no-build：绝不在 NAS 上编译）
docker image tag "$IMG" hvaccrm:local 2>&1
cd "$DIR" || exit 9
docker compose -f docker-compose.yml -p hvaccrm up -d --no-build 2>&1
UP_EXIT=$?
echo "UP_EXIT=$UP_EXIT"
if [ "$UP_EXIT" -ne 0 ]; then
  echo "MARKER=UP_FAIL"
  : > "$DIR/pullmark_UP_FAIL.txt"
  exit 0
fi

# 4) 健康检查（文件名即数据通道，沙箱读不到映射端口也能核对版本）
H=""
i=0
while [ $i -lt 12 ]; do
  H=$(docker exec hvaccrm python3 -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=8).read().decode())" 2>&1)
  case "$H" in
    *'"status":"ok"'*) break ;;
  esac
  i=$((i + 1))
  sleep 5
done
echo "HEALTH=$H"
SAFE=$(printf '%s' "$H" | tr -cd 'A-Za-z0-9._-' | cut -c1-150)
: > "$DIR/health_${SAFE}.txt"
echo "MARKER=PULL_OK"

# 5) 清掉 Docker Hub 名的镜像引用（hvaccrm:local 还引用同一层，不占额外空间）
docker image rm "$IMG" >/dev/null 2>&1 || true

echo "===== END $(date -Iseconds) ====="
exit 0
