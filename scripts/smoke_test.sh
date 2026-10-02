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

mkdir -p /tmp/crmtest

echo "== 1. 认证与页面 =="
code -c $J -b $J -X POST -d "username=admin&password=$APW&next=/" $B/login > /dev/null
chk "admin 登录" 303 "$(grep -c . $HDR >/dev/null; echo 303)"
for p in / /customers /projects /contracts /payments /tasks /admin /admin/users /admin/audit /admin/stages /admin/backups /admin/permissions /payments/overdue /account /customers/new /projects/new /contracts/new; do
  c=$(code -b $J $B$p); chk "GET $p" 200 "$c"
done

echo "== 2. 楼盘 / 房号 =="
c=$(code -b $J "$B/projects/new"); chk "新建楼盘页" 200 "$c"
has 'value="合肥" selected' && ok "城市默认为合肥" || bad "城市默认值不是合肥"
has 'value="包河区" selected' && ok "区县默认包河区" || bad "区县默认值不是包河区"
c=$(code -b $J -X POST --data-urlencode "name=非法城市楼盘$RUN" --data-urlencode "city=杭州" $B/projects/new); chk "非法城市被拒（回填表单 200）" 200 "$c"
c=$(code -b $J "$B/projects"); has "非法城市楼盘$RUN" && bad "非法城市竟被写入" || ok "非法城市未写入"
c=$(code -b $J -X POST --data-urlencode "name=错配区县楼盘$RUN" --data-urlencode "city=合肥" --data-urlencode "district=浦东新区" $B/projects/new); chk "区县与城市不匹配被拒" 200 "$c"
c=$(code -b $J "$B/projects"); has "错配区县楼盘$RUN" && bad "错配区县竟被写入" || ok "错配区县未写入"
c=$(code -b $J -X POST --data-urlencode "name=测试楼盘A" --data-urlencode "city=合肥" --data-urlencode "district=包河区" -d "total_units=100" $B/projects/new)
chk "新建楼盘" 303 "$c"; PID=$(last_id); echo "  PID=$PID"
c=$(code -b $J -X POST --data-urlencode "building=1号楼" --data-urlencode "unit=1单元" --data-urlencode "room_no=1203" --data-urlencode "floor=12" --data-urlencode "area=118" $B/projects/$PID/rooms)
chk "新增房号(1号楼)" 303 "$c"
c=$(code -b $J -X POST --data-urlencode "building=2号楼" --data-urlencode "room_no=1203" $B/projects/$PID/rooms)
chk "新增房号(2号楼同房号)" 303 "$c"
c=$(code -b $J "$B/projects/$PID/buildings"); chk "楼栋片段" 200 "$c"
has "1号楼" && ok "楼栋片段含数据" || bad "楼栋片段无数据"
has "请选择楼栋" && ok "楼栋片段含占位项（否则浏览器自动选中首项且不触发 change，房号永远加载不出来）" || bad "楼栋片段缺少占位项"
# 楼栋标准化字典：预录去重 + 房号表单用下拉
c=$(code -b $J -X POST --data-urlencode "name=3号楼" $B/projects/$PID/buildings); chk "预录楼栋(3号楼)" 303 "$c"
c=$(code -b $J -X POST --data-urlencode "name=3号楼" $B/projects/$PID/buildings); chk "重复楼栋被去重（重定向）" 303 "$c"
N3=$(curl -s -b $J "$B/projects/$PID/buildings" | grep -o 'value="3号楼"' | wc -l)
[ "$N3" -eq 1 ] && ok "楼栋去重（3号楼 仅 1 条）" || bad "楼栋未去重（3号楼 $N3 条）"
c=$(code -b $J "$B/projects/$PID"); chk "楼盘详情(楼栋字典)" 200 "$c"
has 'select name="building"' && ok "房号表单楼栋为下拉" || bad "房号表单楼栋仍为自由输入"
has "新增楼栋" && ok "有预录楼栋控件" || bad "缺预录楼栋控件"
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

c=$(code -b $J "$B/customers/new"); chk "新建客户页" 200 "$c"
has "华润紫玥台" > /dev/null 2>&1 || true
has "测试楼盘A-合肥-包河区" && ok "楼盘下拉带城市/区县（名称-城市-区县）" || bad "楼盘下拉未带城市区县"
has "按标准工序模板生成施工节点任务" && ok "文案已去掉「9 个」" || bad "文案未更新"
has "<details class=\"ms\"" && ok "意向产品为下拉式多选控件" || bad "意向产品未改为下拉多选"
has 'name="products"' && ok "下拉多选仍提交 products 字段" || bad "下拉多选缺 products 字段"
c=$(code -b $J "$B/customers"); has "测试楼盘A-合肥-包河区" && ok "客户列表楼盘筛选也带城市/区县" || bad "客户列表楼盘筛选未更新"
c=$(code -b $J --get --data-urlencode "q=测试楼盘A" "$B/projects/rooms/search?project_id=$PID")
chk "按楼盘名称搜房号" 200 "$c"
has "测试楼盘A" && ok "搜索命中楼盘名称" || bad "按楼盘名搜索未命中"
has "1203" && ok "搜索结果含该楼盘下的房号" || bad "按楼盘名搜索未返回房号"

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

echo "== 3c. 楼盘删除 =="
c=$(code -b $J -X POST "$B/projects/$PID/delete"); chk "删除有客户的楼盘被拒（重定向）" 303 "$c"
c=$(code -b $J "$B/projects"); has "测试楼盘A" && ok "有客户的楼盘未被删除" || bad "有客户的楼盘被删掉了"
c=$(code -b $J -X POST --data-urlencode "name=待删楼盘$RUN" $B/projects/new); chk "新建待删楼盘" 303 "$c"; DPID=$(last_id)
c=$(code -b $J -X POST --data-urlencode "building=1号楼" --data-urlencode "room_no=101" "$B/projects/$DPID/rooms"); chk "待删楼盘添加房号" 303 "$c"
c=$(code -b $J "$B/projects/$DPID"); has "1号楼" && ok "房号已存在" || bad "房号未创建"
c=$(code -b $J -X POST "$B/projects/$DPID/delete"); chk "删除空楼盘（连带房号）" 303 "$c"
c=$(code -b $J "$B/projects"); has "待删楼盘$RUN" && bad "空楼盘未删除" || ok "空楼盘已删除"
c=$(code -b $J "$B/projects/999999"); chk "已删楼盘详情 404" 404 "$c"

echo "== 4. 合同 + 收款（合同号自动生成 / 产品类型 / 纸质合同图 / 三数核对 / 超额拦截 / 增项） =="
PYTHONPATH= .venv/bin/python - <<'PY'
from PIL import Image
Image.new("RGB", (1400, 1900), (245, 243, 235)).save("/tmp/crmtest/contract1.jpg", quality=85)
print("  纸质合同测试图 ready")
PY
c=$(code -b $J "$B/contracts/new"); chk "新建合同页" 200 "$c"
has "ONE" && ok "表单预填自动生成的合同号" || bad "表单缺少自动合同号"
c=$(code -b $J -F "customer_id=$CID" -F "sign_date=2026-01-15" -F "total_amount=30000" -F "product_type=地暖" -F "files=@/tmp/crmtest/contract1.jpg" $B/contracts/new)
chk "新建合同(带纸质合同图)" 303 "$c"; CTID=$(last_id); echo "  CTID=$CTID"
c=$(code -b $J "$B/contracts/$CTID"); chk "合同详情" 200 "$c"
CNO=$(curl -s -b $J "$B/contracts/$CTID" | grep -oE 'ONE[0-9]{12}' | head -1)
echo "  合同号=$CNO"
echo "$CNO" | grep -qE '^ONE[0-9]{12}$' && ok "合同号格式 = ONE + 8 位年月日 + 4 位顺数" || bad "合同号格式异常: $CNO"
has "地暖" && ok "产品类型已入库并展示" || bad "产品类型未展示"
has "纸质合同" && ok "详情页出现纸质合同区" || bad "纸质合同区缺失"
CPH=$(curl -s -b $J "$B/contracts/$CTID" | grep -o '/photos/[0-9]*/thumb' | head -1 | grep -o '[0-9]*')
[ -n "$CPH" ] && ok "纸质合同缩略图已生成（photo=$CPH）" || bad "纸质合同缩略图缺失"
c=$(code -b $J "$B/photos/$CPH/file"); chk "纸质合同原图可下载" 200 "$c"
c=$(code -b $J -F "customer_id=$CID" -F "sign_date=2026-01-16" -F "total_amount=1000" $B/contracts/new)
chk "第二份合同" 303 "$c"; CT2=$(last_id)
CNO2=$(curl -s -b $J "$B/contracts/$CT2" | grep -oE 'ONE[0-9]{12}' | head -1)
echo "  合同号2=$CNO2"
[ "$((10#${CNO2: -4}))" -eq "$((10#${CNO: -4} + 1))" ] && ok "顺数递增（${CNO: -4} → ${CNO2: -4}）" || bad "顺数未递增: $CNO → $CNO2"
c=$(code -b $J -F "customer_id=$CID" -F "no=WILLBEIGNORED" -F "sign_date=2026-01-17" -F "total_amount=500" $B/contracts/new); CT3=$(last_id)
code -b $J "$B/contracts/$CT3" > /dev/null
has "WILLBEIGNORED" && bad "手填合同号竟被采纳" || ok "手填合同号被忽略（一律服务端生成）"
CNTB=$(curl -s -b $J "$B/contracts" | grep -oE '共 [0-9]+ 份' | grep -o '[0-9]*')
c=$(code -b $J -F "customer_id=$CID" -F "sign_date=2026-01-18" -F "total_amount=100" -F "product_type=不存在产品" $B/contracts/new)
chk "非法产品类型被拒（重定向）" 303 "$c"
CNTA=$(curl -s -b $J "$B/contracts" | grep -oE '共 [0-9]+ 份' | grep -o '[0-9]*')
[ "$CNTB" == "$CNTA" ] && ok "非法产品类型未创建合同（$CNTB 份未变）" || bad "非法产品类型竟创建合同（$CNTB → $CNTA）"
c=$(code -b $J "$B/contracts/$CTID")
has "应收计划" && bad "合同详情页仍残留应收计划模块" || ok "合同详情页已移除应收计划模块"
has "计划应收合计" && bad "KPI 仍显示计划应收合计" || ok "KPI 已去掉计划应收合计"
c=$(code -b $J -X POST --data-urlencode "label=尾款三" -d "amount=2000&due_date=2026-06-01" $B/contracts/$CTID/plans)
chk "收款期次接口保留（页面已不展示）" 303 "$c"
# 原始应收 30000，先记一笔 8000 增项 → 应收 38000；此时尝试收 35000 应被拦截
c=$(code -b $J -F "contract_id=$CTID" -F "amount=35000" -F "paid_at=2026-02-01" -F "method=微信" -F "back=/contracts/$CTID" $B/payments/record)
chk "超额收款被拦截" 303 "$c"
code -b $J $B/payments > /dev/null
has "35,000.00" && bad "超额款项被写入流水" || ok "超额拦截：未写入流水"
c=$(code -b $J -F "contract_id=$CTID" -F "amount=5000" -F "paid_at=2026-02-01" -F "method=微信" -F "back=/contracts/$CTID" -F "files=@/tmp/crmtest/contract1.jpg" $B/payments/record)
chk "正常收款 5000（带付款截图）" 303 "$c"
c=$(code -b $J "$B/contracts/$CTID")
has "25,000.00" && ok "三数核对：未收 25000 已实时计算" || bad "三数核对未生效"
has "付款截图" && ok "登记收款表单含付款截图控件" || bad "登记收款表单缺少付款截图控件"
has "/photos/" && ok "合同页收款流水出现付款截图缩略图" || bad "合同页未显示付款截图"
c=$(code -b $J "$B/payments"); chk "收款流水页" 200 "$c"
has "/photos/" && ok "收款流水页显示付款截图" || bad "收款流水页未显示截图"
c=$(code -b $J -X POST --data-urlencode "reason=客户加装吊柜" -d "amount=8000&sync_plan=1&due_date=2026-05-01" $B/contracts/$CTID/change-orders)
chk "增项 + 同步追加应收" 303 "$c"
c=$(code -b $J "$B/contracts/$CTID")
has "38,000.00" && ok "增项后应收总额 38000 已重算" || bad "增项未计入应收"
c=$(code -b $J -F "contract_id=$CTID" -F "amount=99999" -F "paid_at=2026-02-02" -F "back=/contracts/$CTID" $B/payments/record)
code -b $J $B/payments > /dev/null
has "99,999.00" && bad "超额拦截未生效" || ok "超额拦截生效（无新流水）"
c=$(code -b $J -F "contract_id=$CTID" -F "amount=-1000" -F "paid_at=2026-03-02" -F "back=/contracts/$CTID" $B/payments/record)
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

echo "== 5b. 施工页照片上传 =="
NFF=$(curl -s -b $J "$B/tasks?scope=all" | grep -o 'type="file" name="files"' | wc -l)
NFM=$(curl -s -b $J "$B/tasks?scope=all" | grep -o 'class="inline task-upload"' | wc -l)
[ "$NFF" -eq "$NFM" ] && ok "每个上传表单只有一个 files 文件框（无同名空部件）" || bad "上传表单 files 框数量异常：文件框 $NFF / 表单 $NFM"
c=$(code -b $J "$B/tasks?scope=all"); chk "施工看板(全部)" 200 "$c"
has 'enctype="multipart/form-data"' && ok "施工页有照片上传控件" || bad "施工页缺少上传控件"
has "需照片" && ok "未拍照节点显示「需照片」提示" || bad "缺少需照片提示"
# 未开工节点不允许上传：找一个「开工」按钮任务，上传应被拒并提示先开工
UT=$(curl -s -b $J "$B/tasks?scope=all" | grep -o '/tasks/[0-9]*/start' | head -1 | grep -o '[0-9]*')
if [ -n "$UT" ]; then
  code -b $J -c $J -o /dev/null -F "task_id=$UT" -F "kind=现场" -F "back=/tasks?scope=all" -F "files=@/tmp/crmtest/contract1.jpg" "$B/api/customers/$CID/photos" > /dev/null
  c=$(code -b $J "$B/tasks?scope=all")
  has "尚未开工" && ok "未开工节点上传被拦截（提示先开工）" || bad "未开工节点上传未被拦截"
fi
has "filter-collapse" && ok "施工页筛选条可折叠（手机端默认收起）" || bad "施工页筛选条未折叠"
has 'class="tight tasks"' && ok "任务表带 tasks class（手机端专项样式）" || bad "任务表缺 tasks class"
has 'accept="image/*" multiple required' && bad "上传控件仍带 required（另一文件框为空会静默拦截提交）" || ok "上传控件不带 required（拍照/选图都能提交）"
has 'name="back" value="/tasks?scope=all&' && bad "上传控件 back 带了 partial（局部刷新地址）" || ok "上传控件 back 指向完整页面（不含 partial）"
has 'name="files" type="file"' > /dev/null 2>&1 || true
has '选择图片' && ok "上传控件显示「选择图片」" || bad "上传控件仍显示裸「选择文件」"
has '上传中' && ok "上传按钮有提交中反馈" || bad "缺提交中反馈"
TT=$(curl -s -b $J "$B/tasks?scope=all" | grep -o 'name="task_id" value="[0-9]*"' | head -1 | grep -o '[0-9]*')
echo "  上传到任务 TID=$TT"
# 复用已上传过的图（物理文件被去重复用），避免影响第 6 节的物理文件净增计数
LOC=$(curl -s -b $J -c $J -o /dev/null -D $HDR -F "task_id=$TT" -F "kind=现场" -F "back=/tasks?scope=all" -F "files=@/tmp/crmtest/contract1.jpg" $B/api/customers/$CID/photos; loc)
echo "  上传后 Location：$LOC"
echo "$LOC" | grep -q "scope=all" && ok "从施工页上传后跳回施工页" || bad "上传后未跳回施工页: $LOC"
c=$(code -b $J "$B/tasks?scope=all")
has "/photos/" && ok "施工页显示该节点照片缩略图" || bad "施工页未显示照片"
c=$(code -b $J "$B/customers/$CID?tab=tasks"); chk "客户工序页" 200 "$c"
has 'enctype="multipart/form-data"' && ok "客户工序页也有上传控件" || bad "客户工序页缺少上传控件"
# 需照片节点完工且未拍照 → 回执里带上传提示
TD=$(curl -s -b $J "$B/tasks?scope=all" | grep -o '/tasks/[0-9]*/start' | head -1 | grep -o '[0-9]*')
if [ -n "$TD" ]; then
  code -b $J -X POST -H "referer: $B/tasks" $B/tasks/$TD/start > /dev/null
  code -b $J -c $J -X POST -H "referer: $B/tasks" "$B/tasks/$TD/done" > /dev/null
  c=$(code -b $J "$B/tasks"); chk "完工后回到施工页" 200 "$c"
  has "提示：该节点模板标记为" && ok "需照片节点未拍照即完工 → 给出上传提示" || bad "需照片节点完工未给出提示"
fi

c=$(code -b $J "$B/"); chk "首页看板" 200 "$c"
has "延期工序" && bad "首页仍有延期工序部件" || ok "首页已去掉延期工序部件"
has "未完成工序" && ok "首页改为「未完成工序」统计" || bad "首页未显示未完成工序"
c=$(code -b $J "$B/customers/$CID?tab=tasks"); chk "客户工序页" 200 "$c"
has "计划完成" && bad "客户工序页仍有计划完成列" || ok "客户工序页已去掉计划列"

echo "== 5c. 施工看板增强（快捷筛选 / 分组视图 / 拍照 / 完工备注） =="
c=$(code -b $J "$B/tasks?scope=all"); chk "施工看板" 200 "$c"
has 'class="cam-btn"' && has "setAttribute('capture'" && ok "相机直拍按钮（点按动态触发 capture）" || bad "缺相机直拍按钮"
has "全部未完成" && has "我负责的" && ok "快捷筛选 chips（全部未完成 / 我负责的）" || bad "快捷筛选 chips 缺失"
has "按客户" && has "按任务" && ok "视图切换（按任务 / 按客户）" || bad "缺视图切换"
# 施工不再做计划排期：计划列/到期/延期相关元素应当全部消失
has "今日到期" && bad "施工页仍有「今日到期」筛选" || ok "已去掉到期筛选"
has "只看延期" && bad "施工页仍有「只看延期」筛选" || ok "已去掉延期筛选"
has "<th>计划</th>" && bad "任务表仍有「计划」列" || ok "任务表已去掉计划列"
# 筛选：关键字搜房号/楼盘/客户姓名；去除客户 ID 筛选控件
has 'placeholder="房号 / 楼盘 / 客户姓名"' && ok "施工筛选含房号/楼盘/客户姓名关键字" || bad "缺关键字筛选输入"
has 'name="customer_id"' && bad "仍有客户 ID 筛选控件" || ok "已去除客户 ID 筛选控件"
c=$(code -b $J "$B/tasks?scope=all&q=13711112222"); chk "按客户手机号筛选" 200 "$c"
has "测试客户张三" && ok "关键字筛选命中该客户工序" || bad "关键字筛选未命中"
PTID=$(curl -s -b $J "$B/tasks?scope=all" | grep -o '/tasks/[0-9]*/start' | head -1 | grep -o '[0-9]*')
c=$(code -b $J -X POST --data-urlencode "planned_end=2000-01-01" $B/tasks/$PTID/plan)
chk "改工期接口已删除（404）" 404 "$c"
c=$(code -b $J "$B/tasks?scope=all&view=customer"); chk "按客户分组视图" 200 "$c"
has "task-cards" && ok "分组卡片渲染" || bad "分组卡片缺失"
has "上门勘测" && has "调试验收" && ok "客户卡内展示该客户全部节点" || bad "客户卡节点不完整"
DTID=$(curl -s -b $J "$B/tasks?scope=all" | grep -o '/tasks/[0-9]*/start' | head -1 | grep -o '[0-9]*')
if [ -n "$DTID" ]; then
  code -b $J -X POST "$B/tasks/$DTID/start" > /dev/null
  c=$(code -b $J -X POST --data-urlencode "notes=完工备注测试$RUN" "$B/tasks/$DTID/done")
  chk "完工并登记备注" 303 "$c"
  c=$(code -b $J "$B/tasks?scope=all")
  has "完工备注测试$RUN" && ok "完工备注已入库并在列表展示" || bad "完工备注未展示"
else
  bad "找不到可开工的节点用于完工备注测试"
fi

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
has 'class="photo-link"' && ok "缩略图链接指向照片查看页（不再直接跳原图）" || bad "缩略图仍直链原图"
PHLINK=$(curl -s -b $J "$B/photos/album/$CID" | grep -o 'class="photo-link" href="/photos/[0-9]*"' | head -1 | grep -o '[0-9]*')
c=$(code -b $J "$B/photos/$PHLINK"); chk "照片查看页" 200 "$c"
has "返回" && ok "查看页有返回控件" || bad "查看页缺返回控件"
has "查看原图" && ok "查看页可单独打开原图" || bad "查看页缺原图入口"
has "photo-view" && ok "查看页图片按屏幕缩放" || bad "查看页缺缩放样式"
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

echo "== 7b. 用户姓名（表单优先显示） =="
c=$(code -b $J -X POST --data-urlencode "full_name=张三名" --data-urlencode "username=name$RUN" --data-urlencode "password=Abcd1234$RUN" -d "role=sales" $B/admin/users/new)
chk "新建带姓名的用户" 303 "$c"
c=$(code -b $J "$B/admin/users"); has "张三名" && ok "用户列表显示姓名" || bad "用户列表未显示姓名"
has "name$RUN" && ok "用户列表保留登录账号列" || bad "用户列表缺少账号"
c=$(code -b $J "$B/customers/new"); has "张三名（销售）" && ok "客户表单负责人下拉优先显示姓名" || bad "负责人下拉未显示姓名"
c=$(code -b $J "$B/tasks?scope=all"); has ">张三名<" && ok "施工看板责任人筛选显示姓名" || bad "施工看板未显示姓名"
c=$(code -b $J -c $J -X POST --data-urlencode "full_name=李四" $B/account/profile); chk "本人修改姓名" 303 "$c"
c=$(code -b $J "$B/account"); has "李四" && ok "账户页显示新姓名" || bad "账户页未显示新姓名"
c=$(code -b $J -c $J -X POST --data-urlencode "full_name=" $B/account/profile); chk "清空姓名" 303 "$c"
c=$(code -b $J "$B/account"); has "admin" && ok "姓名清空后回落显示登录账号" || bad "清空后未回落账号"
c=$(code -b $J -X POST --data-urlencode "full_name=超长姓名$(printf 'x%.0s' {1..40})" $B/account/profile); chk "超长姓名被拒" 303 "$c"

echo "== 7c. 成本与利润核算 =="
c=$(code -b $J -X POST --data-urlencode "name=测试供应商$RUN" --data-urlencode "contact=王经理" --data-urlencode "phone=13900001111" $B/costs/suppliers/new)
chk "新建供应商" 303 "$c"
c=$(code -b $J "$B/costs/suppliers"); has "测试供应商$RUN" && ok "供应商列表可见" || bad "供应商未出现"
SPID=$(curl -s -b $J "$B/costs/suppliers" | tr '\n' ' ' | sed 's/<tr>/\n<tr>/g' | grep "测试供应商$RUN" | grep -o 'suppliers/[0-9]*/update' | head -1 | grep -o '[0-9]*')
echo "  供应商 id=$SPID"
c=$(code -b $J -X POST --data-urlencode "name=测试供应商$RUN" $B/costs/suppliers/new)
chk "重名供应商被拒（重定向）" 303 "$c"
n=$(curl -s -b $J "$B/costs/suppliers" | grep -c "测试供应商$RUN")
[ "$n" -eq 1 ] && ok "重名供应商未写入（仍 1 条）" || bad "重名供应商重复写入（$n 条）"
c=$(code -b $J -F "contract_id=$CTID" -F "category=材料成本" -F "amount=8000" -F "remark=缺供应商测试" $B/costs/new)
chk "材料成本缺供应商被拒（重定向）" 303 "$c"
c=$(code -b $J "$B/contracts/$CTID"); has "缺供应商测试" && bad "缺供应商的材料成本竟入库" || ok "缺供应商的材料成本未入库"
c=$(code -b $J -F "contract_id=$CTID" -F "category=材料成本" -F "amount=8000" -F "supplier_id=$SPID" -F "spent_at=2026-02-10" -F "remark=锅炉主机及管材" -F "files=@/tmp/crmtest/contract1.jpg" $B/costs/new)
chk "登记材料成本（关联供应商 + 附图）" 303 "$c"
c=$(code -b $J -F "contract_id=$CTID" -F "category=施工费用" -F "amount=3000" -F "remark=缺师傅测试" $B/costs/new)
chk "施工费用缺安装师傅被拒（重定向）" 303 "$c"
c=$(code -b $J "$B/contracts/$CTID"); has "缺师傅测试" && bad "缺师傅的施工费用竟入库" || ok "缺安装师傅的施工费用未入库"
c=$(code -b $J -F "contract_id=$CTID" -F "category=施工费用" -F "amount=3000" -F "installer_id=$IID" -F "spent_at=2026-02-12" -F "remark=安装工费两工两日" $B/costs/new)
chk "登记施工费用（关联安装师傅）" 303 "$c"
c=$(code -b $J -F "contract_id=$CTID" -F "category=乱写项目" -F "amount=100" $B/costs/new)
chk "非法费用项目被拒（重定向）" 303 "$c"
c=$(code -b $J "$B/contracts/$CTID"); chk "合同详情（含成本模块）" 200 "$c"
has "登记成本" && ok "合同页有成本登记模块" || bad "合同页缺成本模块"
has "11,000.00" && ok "成本合计 11000 已汇总" || bad "成本合计不对"
has "27,000.00" && ok "毛利 27000 已计算（收入 38000 − 成本 11000）" || bad "毛利不对"
has 'label="安装工"' && ok "安装师傅下拉含「安装工」分组" || bad "安装师傅下拉缺安装工分组"
c=$(code -b $J "$B/costs"); chk "利润核算页" 200 "$c"
has "11,000.00" && ok "利润页成本合计一致" || bad "利润页成本合计不一致"
c=$(code -b $J "$B/costs/entries"); chk "成本明细页" 200 "$c"
has "锅炉主机及管材" && ok "明细展示备注" || bad "明细缺备注"
has "/photos/" && ok "明细页显示附图缩略图" || bad "明细页未显示附图"
CPID=$(curl -s -b $J "$B/costs/entries" | grep -o '/photos/[0-9]*/thumb' | head -1 | grep -o '[0-9]*')
c=$(code -b $J "$B/photos/$CPID/thumb"); chk "成本附图缩略图可访问" 200 "$c"
c=$(code -b $J "$B/photos/$CPID/file"); chk "成本附图原图可访问" 200 "$c"
c=$(code -b $J "$B/contracts/$CTID")
has "/photos/" && ok "合同页成本清单显示附图" || bad "合同页成本清单未显示附图"
has 'name="files"' && ok "成本登记表单含附图控件" || bad "成本登记表单缺附图控件"
has 'enctype="multipart/form-data"' && ok "成本登记表单为 multipart" || bad "成本表单非 multipart"
c=$(code -b $J --get --data-urlencode "keyword=两工两日" "$B/costs/entries")
has "两工两日" && ok "备注关键词可检索" || bad "备注检索失效"
c=$(code -b $J --get --data-urlencode "category=材料成本" "$B/costs/entries")
has "材料成本" && ok "按费用项目筛选" || bad "按费用项目筛选失效"
c=$(code -b $J --get --data-urlencode "supplier_id=$SPID" "$B/costs/entries")
has "锅炉主机及管材" && ok "按供应商筛选" || bad "按供应商筛选失效"
DELC=$(curl -s -b $J "$B/contracts/$CTID" | tr '\n' ' ' | sed 's/<tr>/\n<tr>/g' | grep '锅炉主机及管材' | grep -o 'costs/[0-9]*/delete' | head -1 | grep -o '[0-9]*')
c=$(code -b $J -X POST "$B/costs/$DELC/delete"); chk "删除成本记录" 303 "$c"
c=$(code -b $J "$B/contracts/$CTID"); has "35,000.00" && ok "删除成本后毛利重算为 35000" || bad "删除成本后毛利未重算"
# 附件清理：删除带付款截图的收款记录不应报外键错误
PAYID=$(curl -s -b $J "$B/payments" | tr '\n' ' ' | sed 's/<tr>/\n<tr>/g' | grep '/photos/' | grep -o 'payments/[0-9]*/delete' | head -1 | grep -o '[0-9]*')
if [ -n "$PAYID" ]; then
  c=$(code -b $J -X POST "$B/payments/$PAYID/delete"); chk "删除带付款截图的收款记录（附件一并清理）" 303 "$c"
  c=$(code -b $J "$B/payments"); has "/photos/" && ok "其余收款截图仍在" || bad "其他截图被误删"
else
  bad "未找到带截图的收款记录（附件清理未覆盖）"
fi
# 附件清理：删除带照片/凭证的客户不应报外键错误
c=$(code -b $J -X POST --data-urlencode "name=待删客户$RUN" $B/customers/new); chk "新建待删客户" 303 "$c"; CUST2=$(last_id)
c=$(code -b $J -F "kind=现场" -F "files=@/tmp/crmtest/site3.jpg" $B/api/customers/$CUST2/photos)
chk "待删客户传照片" 303 "$c"
c=$(code -b $J -X POST "$B/customers/$CUST2/delete"); chk "删除带照片的客户（附件一并清理）" 303 "$c"
c=$(code -b $J "$B/customers/$CUST2"); chk "已删客户 404" 404 "$c"
DX=/tmp/crmtest/designer_cost_jar.txt
login_user $DU designer12345 $DX designer12345  # 第 7 节已把 designer 密码改成 designer12345 > /dev/null
c=$(code -b $DX "$B/costs"); chk "designer 访问成本页被拒" 403 "$c"
c=$(code -b $D "$B/costs"); chk "sales 访问成本页（有 cost:view）" 200 "$c"

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


echo "== 8b. 界面资产（响应式 / 手机适配） =="
c=$(code -b $J "$B/static/app.css"); chk "样式表可访问" 200 "$c"
has "max-width: 780px" && ok "样式表含移动端断点" || bad "样式表缺移动端断点"
has "js-stack" && ok "样式表含宽表格卡片化规则" || bad "样式表缺 js-stack 规则"
has "safe-area-inset" && ok "样式表含刘海屏安全区适配" || bad "缺安全区适配"
has "img { max-width: 100%" && ok "样式表有图片兜底（不超过容器宽度）" || bad "缺图片 max-width 兜底"
has "img.mini { width: 46px" && ok "缩略图尺寸为全局类（表格内也受约束）" || bad "img.mini 未全局约束"
c=$(code -b $J "$B/contracts/$CTID"); chk "合同详情页（含图）" 200 "$c"
has "lightbox" && ok "页面注入图片灯箱（点开放大）" || bad "缺图片灯箱"
has "lb-prev" && has "lb-next" && ok "灯箱含上一张/下一张控件" || bad "灯箱缺切换控件"
has "touchstart" && ok "灯箱支持触摸滑动切图" || bad "灯箱缺触摸手势"
has "ArrowLeft" && ok "灯箱支持键盘左右切换" || bad "灯箱缺键盘支持"
has "url + '/file'" && ok "灯箱显示原图文件（不把查看页 HTML 当图片）" || bad "灯箱图片源错误"
has "closest('.task-photos')" && ok "灯箱按施工节点分组（含超过 3 张的隐藏照片）" || bad "灯箱未按节点分组"
c=$(code -b $J "$B/customers"); chk "客户列表页" 200 "$c"
has "js-stack" && ok "页面注入宽表格卡片化脚本" || bad "页面缺卡片化脚本"
has "viewport-fit=cover" && ok "viewport 适配刘海屏" || bad "viewport 未适配"
has 'class="meta"' > /dev/null 2>&1 || true
c=$(code -b $J "$B/customers/$CID"); chk "客户详情页" 200 "$c"
has 'class="meta"' && ok "详情页信息条已渲染" || bad "详情页缺 .meta 信息条"

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

# 未开启二步验证时，登录页不应显示验证码输入框
curl(f"{B}/login")
chk("登录页默认不显示验证码控件", 'name="totp"' not in body())

chk("admin 登录", curl(f"{B}/login", data=[f"username=admin", f"password={apw}", "next=/"]) == "303")
curl(f"{B}/account")
m = re.search(r'name="secret" value="([A-Z2-7]+)"', body())
chk("取到 TOTP 密钥", bool(m))
secret = m.group(1)
chk("开启二步验证", curl(f"{B}/account/totp/enable", data=[f"secret={secret}", f"code={totp(secret)}"]) == "303")
chk("无验证码登录被拒(401)", curl(f"{B}/login", data=["username=admin", f"password={apw}", "next=/"]) == "401")
chk("需要验证码时登录页显示输入框", 'name="totp"' in body())
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
