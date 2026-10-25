#!/bin/sh
# 部署后验证脚本（临时，用完即删）
# A) 线上 /health           → 文件名编码
# B) 容器内跑新增测试        → 文件名编码（强制 DB_PATH=:memory:，不碰生产库）
# C) 生产库客户状态只读检查  → 文件名编码
# D) build_result.log 关键行 → 文件名编码
DIR=/vol1/1000/Docker/Hvaccrm
rm -f $DIR/health_*.txt $DIR/ptout_*.txt $DIR/dbchk_*.txt $DIR/marker_*.txt $DIR/buildmark_*.txt

# D) 构建标记
grep -E "MARKER=|BUILD_EXIT=|UP_EXIT=|HEALTH=" $DIR/build_result.log 2>/dev/null | tr '\n' ' ' | tr -cd 'A-Za-z0-9._= ' | cut -c1-180 | tr ' ' '_' > $DIR/buildmark_tmp.txt
BM=$(cat $DIR/buildmark_tmp.txt)
: > "$DIR/buildmark_${BM}.txt"

# A) health
H=$(docker exec hvaccrm python3 -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=8).read().decode())" 2>&1)
SAFE=$(printf '%s' "$H" | tr -cd 'A-Za-z0-9._-' | cut -c1-150)
: > "$DIR/health_${SAFE}.txt"

# B) 容器内跑新增测试（内存库）
docker cp $DIR/tests hvaccrm:/tmp/crm_tests >/dev/null 2>&1
docker exec -w /tmp/crm_tests -e DB_PATH=:memory: hvaccrm python3 -m pytest . -q > $DIR/pt.txt 2>&1
PS=$(tr -cd 'A-Za-z0-9._-=' < $DIR/pt.txt | tr ' ' '_' | cut -c1-180)
: > "$DIR/ptout_${PS}.txt"

# C) 生产库只读检查
docker exec hvaccrm python3 -c "
import sqlite3, pathlib
c = sqlite3.connect('file:/data/crm.db?mode=ro', uri=True)
tot = c.execute('select count(*) from customers').fetchone()[0]
won = c.execute(\"select count(*) from customers where status='won'\").fetchone()[0]
recent = c.execute('select id,name,status,created_at from customers order by id desc limit 5').fetchall()
txt = 'total=%s won=%s recent=%s' % (tot, won, recent)
safe = ''.join(ch if (ch.isalnum() or ch in '._-') else '_' for ch in txt)[:180]
pathlib.Path('/tmp/dbchk_' + safe + '.txt').write_text(txt)
" > $DIR/dbchk_err.txt 2>&1
docker exec hvaccrm sh -c 'cat /tmp/dbchk_*.txt' > $DIR/dbchk.txt 2>&1
DS=$(tr -cd 'A-Za-z0-9._-=' < $DIR/dbchk.txt | tr ' ' '_' | cut -c1-180)
: > "$DIR/dbchk_${DS}.txt"

docker exec hvaccrm rm -rf /tmp/crm_tests >/dev/null 2>&1
: > "$DIR/marker_VERIFY_DONE.txt"
exit 0
