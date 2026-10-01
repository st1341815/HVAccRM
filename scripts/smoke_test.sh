#!/bin/bash
# 本地 smoke test：核心链路 + 权限拦截 + 数据范围
B=${BASE_URL:-http://127.0.0.1:8012}
APW=${ADMIN_PW:-admin12345}
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
c=$(code -b $J --get --data-urlencode "building=1号楼" $B/projects/$PID/rooms); chk "房号片段" 200 "$c"
c=$(code -b $J "$B/projects/rooms/search?project_id=$PID&q=1203"); chk "房号模糊搜索" 200 "$c"
has "1203" && ok "模糊搜索命中 1203" || bad "模糊搜索未命中"
n=$(grep -c "1203" $OUT); [ "$n" -ge 2 ] && ok "跨楼栋返回 2 条 1203（实际 $n）" || bad "跨楼栋未返回多条 1203"
c=$(code -b $J -X POST --data-urlencode "name=快捷楼盘B" $B/projects/quick); chk "快捷新建楼盘(JSON)" 200 "$c"
ROOMID=$(curl -s -b $J --get --data-urlencode "building=1号楼" $B/projects/$PID/rooms | grep -o 'value="[0-9]*"' | head -1 | grep -o '[0-9]*')
echo "  ROOMID=$ROOMID"

echo "== 3. 客户 + 自动建工序 + 联系人 =="
c=$(code -b $J -X POST --data-urlencode "name=测试客户张三" --data-urlencode "type=家装业主" --data-urlencode "source=自然到店" --data-urlencode "level=A" --data-urlencode "room_id=$ROOMID" --data-urlencode "contact_name=张三" --data-urlencode "contact_phone=13800000000" $B/customers/new)
chk "新建客户" 303 "$c"; CID=$(last_id); echo "  CID=$CID"
c=$(code -b $J "$B/customers/$CID?tab=tasks"); chk "客户工序页" 200 "$c"
has "售后回访" && ok "9 节点工序自动生成" || bad "工序未生成"
c=$(code -b $J -X POST --data-urlencode "name=李四" --data-urlencode "title=经理" -d "phone=13900000000&is_primary=1" $B/customers/$CID/contacts)
chk "添加联系人" 303 "$c"
c=$(code -b $J --get --data-urlencode "q=张三" $B/customers); chk "搜索客户(FTS5)" 200 "$c"
has "测试客户张三" && ok "搜索结果命中" || bad "搜索结果未命中"
c=$(code -b $J --get --data-urlencode "q=13800000000" $B/customers)
has "测试客户张三" && ok "按联系人手机搜索命中" || bad "手机搜索未命中"
c=$(code -b $J --get --data-urlencode "q=张三" $B/ui/customer-search); chk "HTMX 客户片段" 200 "$c"

echo "== 4. 合同 + 收款（三数核对 / 超额拦截 / 增项） =="
c=$(code -b $J -X POST --data-urlencode "customer_id=$CID" --data-urlencode "no=HT-TEST-001" --data-urlencode "sign_date=2026-01-15" --data-urlencode "total_amount=30000" --data-urlencode "discount=0" --data-urlencode "plan_labels=定金
首期款
尾款" --data-urlencode "plan_amounts=5000
15000
10000" --data-urlencode "plan_dates=2026-02-01
2026-03-01
2026-04-01" $B/contracts/new)
chk "新建合同(3 期计划)" 303 "$c"; CTID=$(last_id); echo "  CTID=$CTID"
c=$(code -b $J "$B/contracts/$CTID"); chk "合同详情" 200 "$c"
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

echo "== 6. 照片上传 / 缩略图 / 去重 =="
PYTHONPATH= .venv/bin/python - <<'PY'
from PIL import Image
im = Image.new("RGB", (2400, 1600), (140, 180, 220))
im.save("/tmp/crmtest/site1.jpg", quality=90)
im.save("/tmp/crmtest/site2.jpg", quality=90)
print("  test images ready")
PY
c=$(code -b $J -F "kind=现场" -F "files=@/tmp/crmtest/site1.jpg" -F "files=@/tmp/crmtest/site2.jpg" $B/api/customers/$CID/photos)
chk "多张上传" 303 "$c"
c=$(code -b $J -F "kind=现场" -F "files=@/tmp/crmtest/site1.jpg" $B/api/customers/$CID/photos)
chk "重复文件再上传(去重)" 303 "$c"
c=$(code -b $J "$B/photos/album/$CID"); chk "相册页" 200 "$c"
PH=$(curl -s -b $J "$B/photos/album/$CID" | grep -o '/photos/[0-9]*/thumb' | head -1 | grep -o '[0-9]*')
c=$(code -b $J "$B/photos/$PH/thumb"); chk "缩略图" 200 "$c"
c=$(code -b $J "$B/photos/$PH/file"); chk "原图" 200 "$c"
if [ -n "$MEDIA_BASE" ]; then
  echo "  物理文件（NAS 侧）："
  NF=$(curl -s -m 10 "$MEDIA_BASE/crm/data/media/$CID/%E7%8E%B0%E5%9C%BA/" | grep -c 'href="[^"?]')
  curl -s -m 10 "$MEDIA_BASE/crm/data/media/$CID/%E7%8E%B0%E5%9C%BA/" | grep -o 'href="[^"]*\.\(jpg\|webp\)"' | head -6
else
  echo "  物理文件："; ls -l "/tmp/crmtest/media/$CID/现场/" | tail -4
  NF=$(ls "/tmp/crmtest/media/$CID/现场/" 2>/dev/null | wc -l)
fi
[ "$NF" -eq 4 ] && ok "3 次上传 4 个物理文件（2 原图 + 2 缩略图，重复只存一份；实际 $NF）" || bad "物理文件数异常: $NF"
c=$(code -b $J -X POST $B/photos/$PH/delete); chk "删除照片" 303 "$c"

echo "== 7. 权限点 / 数据范围 / 金额隔离 =="
code -b $J -X POST -d "username=designer1&password=design12345&role=designer&data_scope=shared" $B/admin/users/new > /dev/null
code -b $J -X POST -d "username=installer1&password=install12345&role=installer&data_scope=self" $B/admin/users/new > /dev/null
code -b $J -X POST -d "username=sales1&password=sales12345&role=sales&data_scope=self" $B/admin/users/new > /dev/null
code -b $J -X POST -d "username=finance1&password=finance12345&role=finance&data_scope=all" $B/admin/users/new > /dev/null

login_user designer1 design12345 $D designer12345 && ok "designer 登录+强制改密" || bad "designer 登录失败"
c=$(code -b $D "$B/customers/$CID"); chk "designer 看未共享客户(shared 范围)" 403 "$c"
c=$(code -b $J -X POST -d "user_id=2" $B/customers/$CID/share); chk "管理员把客户共享给设计师" 303 "$c"
c=$(code -b $D "$B/customers/$CID"); chk "designer 看已共享客户" 200 "$c"
curl -s -b $D "$B/customers/$CID" | grep -q "未收金额" && bad "设计师看到了金额" || ok "金额隔离：设计师看不到合同金额"
c=$(code -b $D $B/contracts); chk "designer 访问合同" 403 "$c"
c=$(code -b $D $B/admin/users); chk "designer 访问用户管理" 403 "$c"
c=$(code -b $D -X POST -d "name=无权限客户" $B/customers/new); chk "designer 新建客户" 403 "$c"

login_user installer1 install12345 $D installer12345 && ok "installer 登录" || bad "installer 登录失败"
c=$(code -b $D $B/customers); chk "installer 客户列表(非白名单)" 403 "$c"
c=$(code -b $D $B/tasks); chk "installer 施工看板" 200 "$c"
c=$(code -b $D $B/payments); chk "installer 收款模块" 403 "$c"
c=$(code -b $D -F "kind=完工" -F "files=@/tmp/crmtest/site2.jpg" $B/api/customers/$CID/photos)
chk "installer 上传照片(被指派外客户)" 403 "$c"

login_user finance1 finance12345 $D finance12345 && ok "finance 登录" || bad "finance 登录失败"
c=$(code -b $D $B/payments); chk "finance 收款流水" 200 "$c"
c=$(code -b $D $B/customers/new); chk "finance 新建客户" 403 "$c"

login_user sales1 sales12345 $D sales12345 && ok "sales 登录" || bad "sales 登录失败"
c=$(code -b $D $B/customers); chk "sales 客户列表" 200 "$c"
curl -s -b $D "$B/customers" | grep -q "测试客户张三" && bad "sales(self) 看到了他人客户" || ok "数据范围 self：销售看不到他人客户"
c=$(code -b $D -X POST --data-urlencode "name=sales自己的客户" $B/customers/new); chk "sales 新建客户" 303 "$c"
c=$(code -b $D "$B/customers")
has "sales自己的客户" && ok "sales 能看到自己新建的客户" || bad "自己客户不可见"
c=$(code -b $D $B/admin); chk "sales 访问系统管理" 403 "$c"

echo "== 8. 会话失效 / 备份 / 审计 =="
login_user sales1 sales12345 $D sales12345x && ok "sales 重新登录" || bad "sales 重新登录失败"
c=$(code -b $D $B/customers); chk "sales 会话有效" 200 "$c"
c=$(code -b $J -X POST -d "data_scope=all&is_active=1" $B/admin/users/4/update)
chk "管理员调整 sales1 数据范围" 303 "$c"
c=$(code -b $D $B/customers); chk "旧会话已失效(跳登录)" 303 "$c"
c=$(code -b $J -X POST $B/admin/backups/run); chk "手动备份" 303 "$c"
ls -1 /tmp/crmtest/backups/ | tail -3
code -b $J $B/admin/audit > /dev/null
has "login" && ok "审计日志已记录登录事件" || bad "审计日志缺失"
has "password_change" && ok "审计日志已记录改密事件" || bad "审计日志缺少改密事件"

echo
echo "===== 结果：通过 $pass，失败 $fail ====="
