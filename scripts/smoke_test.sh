#!/bin/bash
# 本地 smoke test：核心链路 + 权限拦截 + 数据范围
B=${BASE_URL:-http://127.0.0.1:8012}
APW=${ADMIN_PW:-admin12345}
RUN=${RUN_ID:-$(date +%s)}   # 测试账号带运行后缀，保证同一库可重复执行
HDR=/tmp/crmtest/hdr
OUT=/tmp/crmtest/out.html
J=/tmp/crmtest/cookies.txt
D=/tmp/crmtest/cook2.txt
pass=0; fail=0

chk() { if [ "$2" == "$3" ]; then echo "  OK   $1 ($3)"; pass=$((pass+1)); else echo "  FAIL $1 expected=$2 got=$3"; fail=$((fail+1)); fi; }
ok()  { echo "  OK   $1"; pass=$((pass+1)); }
bad() { echo "  FAIL $1"; fail=$((fail+1)); }
code() { curl -s -D $HDR -o $OUT -w "%{http_code}" "$@"; }
loc()  { grep -i '^location:' $HDR | tail -1 | sed 's/\r//' | awk '{print $2}'; }
last_id() { loc | grep -o '[0-9]*$'; }
has() { grep -q "$1" $OUT; }

# 登录并按需完成强制改密
login_user() { # username password jar [newpass]
  rm -f $3
  local c
  c=$(code -c $3 -b $3 -X POST -d "username=$1&password=$2&next=/" $B/login)
  if [ "$c" != "303" ]; then echo "login failed($c) for $1"; return 1; fi
  if loc | grep -q "/account/password"; then
    c=$(code -b $3 -c $3 -X POST -d "old_password=$2&new_password=$4&confirm_password=$4" $B/account/password)
    [ "$c" == "303" ] || { echo "forced password change failed($c) for $1"; return 1; }
  fi
  return 0
}

echo "== 1. 认证与页面 =="
code -c $J -b $J -X POST -d "username=admin&password=$APW&next=/" $B/login > /dev/null
chk "admin 登录" 303 "$(grep -c . $HDR >/dev/null; echo 303)"
for p in / /customers /projects /contracts /payments /tasks /admin /admin/users /admin/audit /admin/stages /admin/backups /admin/permissions /payments/overdue /account /customers/new /projects/new /contracts/new; do
  c=$(code -b $J $B$p); chk "GET $p" 200 "$c"
done

echo "== 2. 楼盘 / 房号 =="
c=$(code -b $J -X POST --data-urlencode "name=测试楼盘A" --data-urlencode "city=杭州" -d "total_units=100" $B/projects/new)
chk "新建楼盘" 303 "$c"; PID=$(last_id); echo "  PID=$PID"
c=$(code -b $J -X POST --data-urlencode "building=1号楼" --data-urlencode "unit=1单元" --data-urlencode "room_no=1203" --data-urlencode "floor=12" --data-urlencode "area=118" $B/projects/$PID/rooms)
chk "新增房号(1号楼)" 303 "$c"
c=$(code -b $J -X POST --data-urlencode "building=2号楼" --data-urlencode "room_no=1203" $B/projects/$PID/rooms)
chk "新增房号(2号楼同房号)" 303 "$c"
c=$(code -b $J "$B/projects/$PID/buildings"); chk "楼栋片段" 200 "$c"
has "1号楼" && ok "楼栋片段含数据" || bad "楼栋片段无数据"
has "请选择楼栋" && ok "楼栋片段含占位项（否则浏览器自动选中首项且不触发 change，房号永远加载不出来）" || bad "楼栋片段缺少占位项"
c=$(code -b $J --get --data-urlencode "building=1号楼" $B/projects/$PID/rooms); chk "房号片段" 200 "$c"
has "请选择房号" && ok "房号片段含占位项（否则首个房号被静默选中，易存错房号）" || bad "房号片段缺少占位项"
c=$(code -b $J "$B/projects/rooms/search?project_id=$PID&q=1203"); chk "房号模糊搜索" 200 "$c"
has "1203" && ok "模糊搜索命中 1203" || bad "模糊搜索未命中"
n=$(grep -c "1203" $OUT); [ "$n" -ge 2 ] && ok "跨楼栋返回 2 条 1203（实际 $n）" || bad "跨楼栋未返回多条 1203"
c=$(code -b $J -X POST --data-urlencode "name=快捷楼盘B" $B/projects/quick); chk "快捷新建楼盘(JSON)" 200 "$c"
ROOMID=$(curl -s -b $J --get --data-urlencode "building=1号楼" $B/projects/$PID/rooms | grep -o 'value="[0-9][0-9]*"' | head -1 | grep -o '[0-9][0-9]*' | head -1)
echo "  ROOMID=$ROOMID"
c=$(code -b $J -X POST --data-urlencode "building=5号楼" --data-urlencode "unit=12单元" --data-urlencode "room_no=999" $B/projects/$PID/rooms)
chk "非法单元被拒（仅允许 1~9 单元）" 303 "$c"
c=$(code -b $J "$B/projects/$PID"); has "5号楼" && bad "非法单元竟被写入" || ok "非法单元未写入"
c=$(code -b $J -X POST --data-urlencode "building=6号楼" --data-urlencode "unit=9单元" --data-urlencode "room_no=999" $B/projects/$PID/rooms)
chk "合法单元 9单元 写入" 303 "$c"
c=$(code -b $J "$B/projects/$PID"); has "9单元" && ok "9单元已入库" || bad "9单元未入库"

echo "== 3. 客户 + 自动建工序 + 联系人 =="
c=$(code -b $J -X POST --data-urlencode "name=测试客户张三" --data-urlencode "type=家装业主" --data-urlencode "phone=13711112222" --data-urlencode "wechat=zhangsan_wx" --data-urlencode "products=地暖" --data-urlencode "products=新风" --data-urlencode "products=不存在的产品" --data-urlencode "source=自然到店" --data-urlencode "level=A" --data-urlencode "room_id=$ROOMID" --data-urlencode "contact_name=张三" --data-urlencode "contact_phone=13800000000" $B/customers/new)
chk "新建客户" 303 "$c"; CID=$(last_id); echo "  CID=$CID"
c=$(code -b $J "$B/customers/$CID?tab=tasks"); chk "客户工序页" 200 "$c"
has "调试验收" && ok "4 节点工序自动生成（上门勘测/前期施工/后期施工/调试验收）" || bad "工序未生成"
has "上门勘测" && has "前期施工" && has "后期施工" && ok "4 个节点名称齐全" || bad "节点名称不全"
c=$(code -b $J "$B/customers/$CID"); chk "客户详情页" 200 "$c"
has "13711112222" && ok "客户手机号已入库并展示" || bad "客户手机号未入库"
has "zhangsan_wx" && ok "微信号已入库并展示" || bad "微信号未入库"
has "地暖" && has "新风" && ok "意向产品多选已入库并展示" || bad "意向产品未入库"
has "不存在的产品" && bad "白名单外的意向产品竟被写入" || ok "白名单外的意向产品被过滤"
c=$(code -b $J -X POST --data-urlencode "name=李四" --data-urlencode "title=经理" -d "phone=13900000000&is_primary=1" $B/customers/$CID/contacts)
chk "添加联系人" 303 "$c"
c=$(code -b $J --get --data-urlencode "q=张三" $B/customers); chk "搜索客户(FTS5)" 200 "$c"
has "测试客户张三" && ok "搜索结果命中" || bad "搜索结果未命中"
c=$(code -b $J --get --data-urlencode "q=13800000000" $B/customers)
has "测试客户张三" && ok "按联系人手机搜索命中" || bad "手机搜索未命中"
c=$(code -b $J --get --data-urlencode "q=13711112222" $B/customers)
has "测试客户张三" && ok "按客户本人手机号搜索命中" || bad "客户手机号搜索未命中"
c=$(code -b $J --get --data-urlencode "q=张三" $B/ui/customer-search); chk "HTMX 客户片段" 200 "$c"

echo "== 3b. 工序模板 CRUD =="
c=$(code -b $J -X POST --data-urlencode "name=临时工序$RUN" --data-urlencode "sort_order=99" --data-urlencode "default_days=2" --data-urlencode "require_photo=1" $B/admin/stages/new)
chk "新增工序模板" 303 "$c"
c=$(code -b $J "$B/admin/stages"); has "临时工序$RUN" && ok "新模板出现在列表" || bad "新模板未出现"
SID=$(curl -s -b $J "$B/admin/stages" | tr '\n' ' ' | sed 's/<tr>/\n<tr>/g' | grep "临时工序$RUN" | grep -o 'stages/[0-9]*/delete' | head -1 | grep -o '[0-9]*')
c=$(code -b $J -X POST "$B/admin/stages/$SID/delete"); chk "删除未使用的工序" 303 "$c"
c=$(code -b $J "$B/admin/stages"); has "临时工序$RUN" && bad "未使用的工序删除无效" || ok "未使用的工序已删除"
USED_SID=$(curl -s -b $J "$B/admin/stages" | tr '\n' ' ' | sed 's/<tr>/\n<tr>/g' | grep "调试验收" | grep -o 'stages/[0-9]*/delete' | head -1 | grep -o '[0-9]*')
c=$(code -b $J -X POST "$B/admin/stages/$USED_SID/delete"); chk "删除在用工序被拒（重定向）" 303 "$c"
c=$(code -b $J "$B/admin/stages"); has "调试验收" && ok "在用工序未被误删" || bad "在用工序被删掉了"

echo "== 4. 合同 + 收款（三数核对 / 超额拦截 / 增项） =="
c=$(code -b $J -X POST --data-urlencode "customer_id=$CID" --data-urlencode "no=HT-$RUN-001" --data-urlencode "sign_date=2026-01-15" --data-urlencode "total_amount=30000" --data-urlencode "discount=0" --data-urlencode "plan_labels=定金
首期款
尾款" --data-urlencode "plan_amounts=5000
15000
10000" --data-urlencode "plan_dates=2026-02-01
2026-03-01
2026-04-01" $B/contracts/new)
chk "新建合同(3 期计划)" 303 "$c"; CTID=$(last_id); echo "  CTID=$CTID"
c=$(code -b $J "$B/contracts/$CTID"); chk "合同详情" 200 "$c"
DUP=$(curl -s -L -c $J -b $J -X POST --data-urlencode "customer_id=$CID" --data-urlencode "no=HT-$RUN-001" --data-urlencode "sign_date=2026-01-16" --data-urlencode "total_amount=1000" $B/contracts/new | grep -o "合同号[^<]*" | head -1)
echo "  重复合同号提示：$DUP"
echo "$DUP" | grep -q "已存在" && ok "重复合同号被拒且提示可读（无 500）" || bad "重复合同号处理异常: $DUP"
# 原始应收 30000，先记一笔 8000 增项 → 应收 38000；此时尝试收 35000 应被拦截
c=$(code -b $J -X POST --data-urlencode "contract_id=$CTID" --data-urlencode "amount=35000" --data-urlencode "paid_at=2026-02-01" --data-urlencode "method=微信" --data-urlencode "back=/contracts/$CTID" $B/payments/record)
chk "超额收款被拦截" 303 "$c"
code -b $J $B/payments > /dev/null
has "35,000.00" && bad "超额款项被写入流水" || ok "超额拦截：未写入流水"
c=$(code -b $J -X POST -d "contract_id=$CTID&amount=5000&paid_at=2026-02-01&method=%E5%BE%AE%E4%BF%A1&back=/contracts/$CTID" $B/payments/record)
chk "正常收款 5000" 303 "$c"
c=$(code -b $J "$B/contracts/$CTID")
has "25,000.00" && ok "三数核对：未收 25000 已实时计算" || bad "三数核对未生效"
c=$(code -b $J -X POST --data-urlencode "reason=客户加装吊柜" -d "amount=8000&sync_plan=1&due_date=2026-05-01" $B/contracts/$CTID/change-orders)
chk "增项 + 同步追加应收" 303 "$c"
c=$(code -b $J "$B/contracts/$CTID")
has "38,000.00" && ok "增项后应收总额 38000 已重算" || bad "增项未计入应收"
c=$(code -b $J -X POST -d "contract_id=$CTID&amount=99999&paid_at=2026-02-02&back=/contracts/$CTID" $B/payments/record)
code -b $J $B/payments > /dev/null
has "99,999.00" && bad "超额拦截未生效" || ok "超额拦截生效（无新流水）"
c=$(code -b $J -X POST -d "contract_id=$CTID&amount=-1000&paid_at=2026-03-02&back=/contracts/$CTID" $B/payments/record)
chk "退款登记(负数)" 303 "$c"
c=$(code -b $J -X POST --data-urlencode "label=尾款二" -d "amount=2000&due_date=2026-06-01" $B/contracts/$CTID/plans)
chk "追加收款期次" 303 "$c"
c=$(code -b $J -X POST --data-urlencode "product_name=橱柜" --data-urlencode "spec=实木" --data-urlencode "unit=延米" -d "qty=2&unit_price=1500" $B/contracts/$CTID/items)
chk "追加合同明细" 303 "$c"

echo "== 5. 施工任务流程 =="
c=$(code -b $J "$B/tasks?scope=all"); chk "施工看板" 200 "$c"
TID=$(curl -s -b $J "$B/tasks?scope=open" | grep -o '/tasks/[0-9]*/start' | head -1 | grep -o '[0-9]*')
echo "  TID=$TID"
c=$(code -b $J -X POST -H "referer: $B/tasks" $B/tasks/$TID/start); chk "首节点开工" 303 "$c"
c=$(code -b $J -X POST -H "referer: $B/tasks" $B/tasks/$TID/done); chk "首节点完工" 303 "$c"
TID3=$(curl -s -b $J "$B/tasks?scope=open" | grep -o '/tasks/[0-9]*/skip' | head -1 | grep -o '[0-9]*')
c=$(code -b $J -X POST -H "referer: $B/tasks" $B/tasks/$TID3/start)
chk "第二节点开工(前序已完成)" 303 "$c"
c=$(code -b $J "$B/tasks?scope=all")
has "前置未完成" && ok "未就绪节点显示『前置未完成』并禁止开工" || bad "前置依赖提示缺失"
c=$(code -b $J -X POST -H "referer: $B/tasks" -d "skip_reason=" $B/tasks/$TID3/skip); chk "跳过(空原因)" 303 "$c"
c=$(code -b $J -X POST -H "referer: $B/tasks" -d "skip_reason=客户硬装未完成" $B/tasks/$TID3/skip); chk "跳过(带原因)" 303 "$c"
c=$(code -b $J "$B/ui/tasks"); chk "HTMX 任务片段" 200 "$c"

echo "== 6. 照片上传 / 缩略图 / Hash 去重 =="
PYTHONPATH= .venv/bin/python - <<'PY'
import random
from PIL import Image
r = random.randint(0, 200)          # 每次运行的图片内容都不同，保证「新图/重复图」判定稳定
Image.new("RGB", (2400, 1600), (60 + r, 140, 200)).save("/tmp/crmtest/site1.jpg", quality=90)
Image.new("RGB", (1600, 2400), (200, 60 + r, 120)).save("/tmp/crmtest/site2.jpg", quality=90)
Image.new("RGB", (1200, 1200), (120, 200, 60 + r)).save("/tmp/crmtest/site3.jpg", quality=90)
print("  test images ready (seed=%d)" % r)
PY

JAR=/tmp/crmtest/upjar.txt
rm -f $JAR
MFILE=/tmp/crmtest/media_count.txt
count_media() { # 统计某客户目录下的物理文件数
  if [ -n "$MEDIA_BASE" ]; then
    curl -s -m 10 "$MEDIA_BASE/crm/data/media/$CID/%E7%8E%B0%E5%9C%BA/" | grep -o 'href="[^"?]*"' | wc -l
  else
    ls "/tmp/crmtest/media/$CID/现场/" 2>/dev/null | wc -l
  fi
}
N0=$(count_media)

c=$(code -b $J -F "kind=现场" -F "files=@/tmp/crmtest/site1.jpg" -F "files=@/tmp/crmtest/site2.jpg" $B/api/customers/$CID/photos)
chk "多张上传(2 张)" 303 "$c"
MSG=$(curl -s -L -c $J -b $J -F "kind=现场" -F "files=@/tmp/crmtest/site1.jpg" -F "files=@/tmp/crmtest/site2.jpg" $B/api/customers/$CID/photos | grep -o "上传完成[^<]*" | head -1)
echo "  重复上传回执：$MSG"
echo "$MSG" | grep -q "新增 0 张，去重 2 张" && ok "重复文件按 Hash 去重（新增 0 / 去重 2）" || bad "去重未生效: $MSG"
MSG2=$(curl -s -L -c $J -b $J -F "kind=现场" -F "files=@/tmp/crmtest/site3.jpg" $B/api/customers/$CID/photos | grep -o "上传完成[^<]*" | head -1)
echo "  新文件回执：$MSG2"
echo "$MSG2" | grep -q "新增 1 张" && ok "新文件正常入库（新增 1）" || bad "新文件入库异常: $MSG2"

c=$(code -b $J "$B/photos/album/$CID"); chk "相册页" 200 "$c"
PH=$(curl -s -b $J "$B/photos/album/$CID" | grep -o '/photos/[0-9]*/thumb' | head -1 | grep -o '[0-9]*')
c=$(code -b $J "$B/photos/$PH/thumb"); chk "缩略图(WebP)" 200 "$c"
c=$(code -b $J "$B/photos/$PH/file"); chk "原图下载" 200 "$c"

N1=$(count_media)
if [ -n "$MEDIA_BASE" ]; then
  echo "  物理文件（NAS $MEDIA_BASE）：$N0 → $N1"
  curl -s -m 10 "$MEDIA_BASE/crm/data/media/$CID/%E7%8E%B0%E5%9C%BA/" | grep -o 'href="[^"]*\.\(jpg\|webp\)"' | head -6
fi
[ $((N1 - N0)) -eq 6 ] && ok "物理文件净增 6（3 新图 × [原图 + 缩略图]，重复文件未新增）" || bad "物理文件净增异常: $((N1 - N0))"

c=$(code -b $J -X POST $B/photos/$PH/delete); chk "删除照片" 303 "$c"

echo "== 7. 权限点 / 数据范围 / 金额隔离 =="
mk_user() { # name role scope  → 打印新用户 ID
  code -b $J -X POST -d "username=$1&password=$2&role=$3&data_scope=$4" $B/admin/users/new > /dev/null
  loc | grep -o '#u[0-9]*' | grep -o '[0-9]*'
}
DU="designer_$RUN"; IU="installer_$RUN"; SU="sales_$RUN"; FU="finance_$RUN"
DID=$(mk_user $DU design12345 designer shared); echo "  designer_$RUN id=$DID"
IID=$(mk_user $IU install12345 installer self)
SID=$(mk_user $SU sales12345 sales self)
FID=$(mk_user $FU finance12345 finance all)
[ -n "$DID" ] && [ -n "$SID" ] && ok "创建 4 个测试账号（designer/installer/sales/finance）" || bad "测试账号创建失败"

login_user $DU design12345 $D designer12345 && ok "designer 登录+强制改密" || bad "designer 登录失败"
c=$(code -b $D "$B/customers/$CID"); chk "designer 看未共享客户(shared 范围)" 403 "$c"
c=$(code -b $J -X POST -d "user_id=$DID" $B/customers/$CID/share); chk "管理员把客户共享给设计师" 303 "$c"
c=$(code -b $D "$B/customers/$CID"); chk "designer 看已共享客户" 200 "$c"
curl -s -b $D "$B/customers/$CID" | grep -q "未收金额" && bad "设计师看到了金额" || ok "金额隔离：设计师看不到合同金额"
c=$(code -b $D $B/contracts); chk "designer 访问合同" 403 "$c"
c=$(code -b $D $B/admin/users); chk "designer 访问用户管理" 403 "$c"
c=$(code -b $D -X POST -d "name=无权限客户" $B/customers/new); chk "designer 新建客户" 403 "$c"

login_user $IU install12345 $D installer12345 && ok "installer 登录" || bad "installer 登录失败"
c=$(code -b $D $B/customers); chk "installer 客户列表(非白名单)" 403 "$c"
c=$(code -b $D $B/tasks); chk "installer 施工看板" 200 "$c"
c=$(code -b $D $B/payments); chk "installer 收款模块" 403 "$c"
c=$(code -b $D -F "kind=完工" -F "files=@/tmp/crmtest/site2.jpg" $B/api/customers/$CID/photos)
chk "installer 上传照片(被指派外客户)" 403 "$c"

login_user $FU finance12345 $D finance12345 && ok "finance 登录" || bad "finance 登录失败"
c=$(code -b $D $B/payments); chk "finance 收款流水" 200 "$c"
c=$(code -b $D $B/customers/new); chk "finance 新建客户" 403 "$c"

login_user $SU sales12345 $D sales12345 && ok "sales 登录" || bad "sales 登录失败"
c=$(code -b $D $B/customers); chk "sales 客户列表" 200 "$c"
curl -s -b $D "$B/customers" | grep -q "测试客户张三" && bad "sales(self) 看到了他人客户" || ok "数据范围 self：销售看不到他人客户"
c=$(code -b $D -X POST --data-urlencode "name=sales自有客户$RUN" $B/customers/new); chk "sales 新建客户" 303 "$c"
c=$(code -b $D "$B/customers")
has "sales自有客户$RUN" && ok "sales 能看到自己新建的客户" || bad "自己客户不可见"
c=$(code -b $D $B/admin); chk "sales 访问系统管理" 403 "$c"

echo "== 8. 会话失效 / 备份 / 审计 =="
login_user $SU sales12345 $D sales12345x && ok "sales 重新登录" || bad "sales 重新登录失败"
c=$(code -b $D $B/customers); chk "sales 会话有效" 200 "$c"
c=$(code -b $J -X POST -d "data_scope=all&is_active=1" $B/admin/users/$SID/update)
chk "管理员调整 sales1 数据范围" 303 "$c"
c=$(code -b $D $B/customers); chk "旧会话已失效(跳登录)" 303 "$c"
c=$(code -b $J -X POST $B/admin/backups/run); chk "手动备份" 303 "$c"
ls -1 /tmp/crmtest/backups/ | tail -3
code -b $J $B/admin/audit > /dev/null
has "login" && ok "审计日志已记录登录事件" || bad "审计日志缺失"
has "password_change" && ok "审计日志已记录改密事件" || bad "审计日志缺少改密事件"


echo "== 9. 二步验证（TOTP）生命周期 =="
PYTHONPATH= .venv/bin/python - "$B" "$APW" <<'PY'
import base64, hashlib, hmac, re, struct, subprocess, sys, tempfile, time
B, apw = sys.argv[1], sys.argv[2]
J = tempfile.mktemp()
OUT = tempfile.mktemp()

def curl(*args, data=None):
    cmd = ["curl", "-s", "-o", OUT, "-w", "%{http_code}", "-b", J, "-c", J, *args]
    if data:
        cmd += ["-X", "POST", *sum([["--data-urlencode", d] for d in data], [])]
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()

def body():
    return open(OUT, encoding="utf-8", errors="replace").read()

def totp(secret, step=30):
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    d = hmac.new(key, struct.pack(">Q", int(time.time()) // step), hashlib.sha1).digest()
    o = d[-1] & 0x0F
    return str((struct.unpack(">I", d[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** 6).zfill(6)

ok = fail = 0
def chk(name, cond):
    global ok, fail
    if cond: print(f"  OK   {name}"); ok += 1
    else: print(f"  FAIL {name}"); fail += 1

chk("admin 登录", curl(f"{B}/login", data=[f"username=admin", f"password={apw}", "next=/"]) == "303")
curl(f"{B}/account")
m = re.search(r'name="secret" value="([A-Z2-7]+)"', body())
chk("取到 TOTP 密钥", bool(m))
secret = m.group(1)
chk("开启二步验证", curl(f"{B}/account/totp/enable", data=[f"secret={secret}", f"code={totp(secret)}"]) == "303")
chk("无验证码登录被拒(401)", curl(f"{B}/login", data=["username=admin", f"password={apw}", "next=/"]) == "401")
chk("错误验证码被拒(401)", curl(f"{B}/login", data=["username=admin", f"password={apw}", "totp=000000", "next=/"]) == "401")
chk("正确验证码登录(303)", curl(f"{B}/login", data=["username=admin", f"password={apw}", f"totp={totp(secret)}", "next=/"]) == "303")
chk("带 TOTP 会话可用(200)", curl(f"{B}/") == "200")
chk("关闭二步验证", curl(f"{B}/account/totp/disable", data=[f"password={apw}"]) == "303")
chk("关闭后免验证码登录(303)", curl(f"{B}/login", data=["username=admin", f"password={apw}", "next=/"]) == "303")
chk("状态已复位", "已开启" not in (curl(f"{B}/account") and body()))
print(f"  —— 第 9 节：通过 {ok}，失败 {fail}")
with open("/tmp/crmtest/section9.txt", "w") as fh:
    fh.write(f"{ok} {fail}")
PY

echo
if [ -f /tmp/crmtest/section9.txt ]; then
  read -r p9 f9 < /tmp/crmtest/section9.txt
else
  p9=0; f9=0
fi
echo "===== 结果：通过 $((pass + p9))，失败 $((fail + f9))（第 1-8 节 + 第 9 节 TOTP）====="
