#!/bin/sh
# HVAccRM 无网同步部署（部署辅助脚本，用完即删）
# 场景：基础镜像 python:3.12-slim 被清理、Docker Hub 拉不动，无法 compose build。
#       本次只改静态资源 / 模板 / 版本号，无依赖变化 → 直接 cp 进容器，验证后 commit 固化。
S=/vol1/1000/Docker/Hvaccrm
exec > $S/sync_result.log 2>&1
apk add --no-cache docker-cli >/dev/null 2>&1

echo "== 1) 存回滚点（当前线上 1.13.0）=="
docker image tag hvaccrm:local hvaccrm:pre-1.13.1 && echo ROLLBACK_TAG_OK

echo "== 2) 同步文件进容器 =="
for f in app/static/app.css app/static/manifest.webmanifest app/static/service-worker.js \
         app/templates/layout.html app/__init__.py; do
  docker cp "$S/$f" "hvaccrm:/app/$f" && echo "cp $f OK"
done

echo "== 3) 重启 =="
docker restart hvaccrm

echo "== 4) health（文件名即数据）=="
H=""; i=0
while [ $i -lt 12 ]; do
  H=$(docker exec hvaccrm python3 -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=8).read().decode())" 2>&1)
  case "$H" in *'"status":"ok"'*) break ;; esac
  i=$((i + 1))
  sleep 5
done
echo "HEALTH=$H"
SAFE=$(printf '%s' "$H" | tr -cd 'A-Za-z0-9._-' | cut -c1-150)
rm -f $S/health_*.txt
: > "$S/health_${SAFE}.txt"

echo "== 5) 版本对得上才 commit 固化 =="
case "$H" in
  *'"version":"1.13.1"'*)
    docker commit hvaccrm hvaccrm:local && echo COMMIT_OK
    : > $S/marker_SYNC_OK.txt
    ;;
  *)
    echo "版本不是 1.13.1，不 commit，容器保持旧镜像可回滚"
    : > $S/marker_SYNC_FAIL.txt
    ;;
esac
echo "== END =="
