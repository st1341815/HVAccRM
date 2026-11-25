#!/bin/sh
# 部署后自验证 + 自清理（用完即删）
# 沙箱连不上 NAS 的映射端口，所以把校验结果编码进文件名，外部用 file ls 读回。
S=/vol1/1000/Docker/Hvaccrm
apk add --no-cache docker-cli >/dev/null 2>&1

FB=$(docker exec hvaccrm grep -c -- '--field-bg' /app/app/static/app.css 2>/dev/null)
DK=$(docker exec hvaccrm grep -c 'html\[data-theme="dark"\]' /app/app/static/app.css 2>/dev/null)
TT=$(docker exec hvaccrm grep -c themeToggle /app/app/templates/layout.html 2>/dev/null)
SW=$(docker exec hvaccrm grep -o 'hvaccrm-shell-v[0-9]*' /app/app/static/service-worker.js 2>/dev/null | head -1)
PR=$(docker exec hvaccrm grep -c '@media print' /app/app/static/app.css 2>/dev/null)
HC=$(docker exec hvaccrm python3 -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=8).read().decode())" 2>&1)
SAFE=$(printf '%s' "$HC" | tr -cd 'A-Za-z0-9._-' | cut -c1-120)

rm -f $S/health_*.txt
: > "$S/health_${SAFE}.txt"
: > "$S/verify_fb${FB}_dk${DK}_tt${TT}_pr${PR}_${SW}.txt"

# 清理临时产物（先删文件，最后删自己——rm 掉正在跑这个脚本的容器会中断后续行）
rm -f $S/run_build.sh $S/run_sync.sh $S/sync_result.log $S/verify.log $S/marker_SYNC_OK.txt
docker rm -f crmsync >/dev/null 2>&1
