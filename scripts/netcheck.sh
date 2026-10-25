#!/usr/bin/env bash
# HVAccRM / 本机通用 —— 网络三层自检
# 用途：网络环境变动后，一条命令判断问题出在 DNS 层 / TUN 路由层 / 出口层
#
# 用法：
#   bash scripts/netcheck.sh            # 完整检查（含 git ls-remote）
#   bash scripts/netcheck.sh --fast     # 跳过 git ls-remote
#
# 退出码：0=全通  1=有问题
set -u

FAST=0
[ "${1:-}" = "--fast" ] && FAST=1

GH=github.com
API=api.github.com
REG=registry-1.docker.io
AUTH=auth.docker.io
JSD=cdn.jsdelivr.net

ok()   { printf '  \033[32m✅\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m❌\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m⚠️ \033[0m %s\n' "$*"; }
head_() { printf '\n\033[1m%s\033[0m\n' "$*"; }

FAIL=0
note() { FAIL=$((FAIL+1)); }

# ---------- 工具 ----------
resolve_local() { getent hosts "$1" 2>/dev/null | awk '{print $1}' | head -1; }
is_fakeip()     { case "${1:-}" in 198.18.*|198.19.*) return 0;; *) return 1;; esac; }
doh_ip() {
  curl -s --max-time 6 "https://dns.alidns.com/resolve?name=$1&type=A" 2>/dev/null \
    | grep -o '"data":"[0-9.]*"' | head -1 | cut -d'"' -f4
}
tcp_open() { timeout 5 bash -c "echo > /dev/tcp/$1/443" 2>/dev/null; }
https_code() {
  local c
  c=$(timeout 12 curl -s -o /dev/null -w '%{http_code}' "$1" 2>/dev/null)
  [ -z "$c" ] && c=000
  case "$c" in 000*) echo 000;; *) echo "$c";; esac
}
tls_ok() {  # $1=ip $2=sni —— 必须真的收到证书（GFW 会在 TLS 阶段 RST，此时 openssl 仍打印 Verify 0 (ok)）
  local out
  out=$(timeout 10 openssl s_client -connect "$1:443" -servername "$2" </dev/null 2>&1)
  echo "$out" | grep -q 'BEGIN CERTIFICATE' && ! echo "$out" | grep -q 'read 0 bytes'
}

printf '\033[1m=== 网络三层自检 · %s ===\033[0m\n' "$(date '+%F %T')"

# ================= 第 1 层：DNS =================
head_ "[1/3] DNS 层"
declare -A LOCAL_IP DOH_IP
FAKE_CNT=0
for d in $GH $API $REG $AUTH $JSD; do
  ip=$(resolve_local "$d")
  LOCAL_IP[$d]="${ip:-<解析失败>}"
  if is_fakeip "$ip"; then
    FAKE_CNT=$((FAKE_CNT+1))
    warn "$d → ${ip}  (fake-ip，被代理接管)"
  else
    ok "$d → ${ip:-解析失败}"
  fi
done
# 对照真实 IP
if [ "$FAKE_CNT" -gt 0 ]; then
  echo "  ── DoH 查真实 IP（对照）──"
  for d in $GH $REG; do
    r=$(doh_ip "$d")
    DOH_IP[$d]="$r"
    if [ -n "$r" ]; then echo "     $d 真实 IP = $r"; else warn "$d DoH 查询失败（阿里 DoH 也不通？）"; fi
  done
fi

# ================= 第 2 层：TUN / 路由 =================
head_ "[2/3] TUN 与路由层"
TUN_IF=$(ip -br addr 2>/dev/null | awk '{print $1}' | grep -Ei 'tun|utun|clash|meta|mihomo|sing|wg' | tr '\n' ' ')
if [ -n "$TUN_IF" ]; then ok "TUN 接口: $TUN_IF"; else warn "没有 TUN/代理虚拟网卡（tun/utun/clash/meta）"; fi

TUN_ROUTE=$(ip route 2>/dev/null | grep -E '^198\.(18|19)\.' | head -3)
if [ -n "$TUN_ROUTE" ]; then ok "fake-ip 路由存在:"; echo "$TUN_ROUTE" | sed 's/^/       /'
else
  if [ "$FAKE_CNT" -gt 0 ]; then
    bad "DNS 返回 fake-ip，但**没有 198.18/198.19 的路由** → DNS 被接管、包没进代理"
    note
  else
    ok "无 fake-ip 路由（正常，未使用 TUN 模式）"
  fi
fi

PORTS=""
for p in 7890 7891 7892 7897 1080 10808 20171 6152; do
  if timeout 1 bash -c "echo > /dev/tcp/127.0.0.1/$p" 2>/dev/null; then PORTS="$PORTS $p"; fi
done
if [ -n "$PORTS" ]; then ok "本地代理端口在听:$PORTS"; else warn "本地无常见代理端口（7890/7891/1080/10808…）"; fi

DEF=$(ip route 2>/dev/null | awk '/^default/{print $3" via "$5; exit}')
echo "     默认路由: ${DEF:-无}"
ENVPROXY=$(env | grep -iE '^(http|https|all)_proxy=' | tr '\n' ' ')
[ -n "$ENVPROXY" ] && echo "     环境代理变量: $ENVPROXY" || echo "     环境代理变量: 无"

# ================= 第 3 层：出口 =================
head_ "[3/3] 出口层（TCP / TLS / HTTPS）"
declare -A HTTPS_CODE
for d in $GH $API $REG $AUTH $JSD; do
  code=$(https_code "https://$d/")
  HTTPS_CODE[$d]=$code
  if [ "$code" = "200" ] || [ "$code" = "301" ] || [ "$code" = "302" ] || [ "$code" = "401" ] || [ "$code" = "404" ]; then
    ok "$d HTTPS → $code"
  else
    bad "$d HTTPS → $code"
    note
  fi
done

# TCP / TLS 分层定位（对 GitHub 做样例）
gh_ip="${DOH_IP[$GH]:-140.82.113.3}"
if [ -n "$gh_ip" ]; then
  if tcp_open "$gh_ip"; then
    ok "TCP $gh_ip:443 可建连"
    if tls_ok "$gh_ip" "$GH"; then ok "TLS 握手成功（SNI=$GH）"
    else bad "TCP 通但 **TLS 被中断** → 出口层 SNI/IP 阻断"; note; fi
  else
    bad "TCP $gh_ip:443 不可达 → 出口层直接不通"; note
  fi
fi

# ================= git 层（可选） =================
if [ "$FAST" -eq 0 ] && command -v git >/dev/null 2>&1; then
  head_ "[附加] git 协议（smart HTTP）"
  if [ -d .git ]; then
    out=$(GIT_TERMINAL_PROMPT=0 timeout 25 git ls-remote origin 2>&1 | head -2)
    if echo "$out" | grep -qi 'refs/heads'; then
      ok "git ls-remote origin 成功"
      echo "$out" | sed 's/^/       /'
    else
      bad "git ls-remote origin 失败"; echo "$out" | sed 's/^/       /'; note
    fi
  else
    echo "  （当前目录不是 git 仓库，跳过）"
  fi
fi

# ================= 结论 =================
head_ "结论"
if [ "$FAIL" -eq 0 ]; then
  printf '  \033[32m网络正常：GitHub / Docker Hub 均可访问\033[0m\n'
  exit 0
fi

TLS_BLOCKED=0
if [ -n "${gh_ip:-}" ] && tcp_open "$gh_ip" && ! tls_ok "$gh_ip" "$GH"; then TLS_BLOCKED=1; fi

GH_OK=0;     [ "${HTTPS_CODE[$GH]}" = "200" ]  && GH_OK=1
DOCKER_OK=0; [ "${HTTPS_CODE[$REG]}" = "200" ] || [ "${HTTPS_CODE[$REG]}" = "401" ] || [ "${HTTPS_CODE[$AUTH]}" = "200" ] && DOCKER_OK=1

# 分层报问题
if [ "$GH_OK" -eq 0 ]; then
  if [ "$TLS_BLOCKED" -eq 1 ]; then
    printf '  \033[31m【问题】出口层：TCP 能连，TLS 握手被中断（SNI/IP 级阻断）\033[0m\n'
    echo "  → 真实 IP 直连也握不上手 → 流量没走代理，被墙了"
  elif [ "$FAKE_CNT" -gt 0 ] && [ -z "$TUN_ROUTE" ]; then
    printf '  \033[31m【问题】DNS 层：解析成 fake-ip（198.18/198.19），但没有 TUN 接口/路由\033[0m\n'
    echo "  → 代理只接管了 DNS，没接管流量（TUN 未建）"
    echo "  → 检查代理程序是否启动（TUN 接口 + 198.18 路由 + 进程三者要同时在）"
    echo "  → 或把 DNS 改回真实 DNS（223.5.5.5 / 119.29.29.29）"
  else
    printf '  \033[31m【问题】出口层不通（DNS 正常，但 HTTPS 全部失败）\033[0m\n'
    echo "  → 查上游网络/网关（当前默认路由：${DEF:-未知}）"
  fi
else
  printf '  \033[32mGitHub 可访问\033[0m（DNS 虽是 fake-ip，但转发已生效——代理工作在网关/透明模式，无需 TUN）\n'
fi

if [ "$DOCKER_OK" -eq 0 ]; then
  printf '  \033[31m【问题】Docker Hub 不可达\033[0m（registry-1.docker.io / auth.docker.io）\n'
  echo "  → 不影响 git 推送；但本地 docker push / docker login 会失败"
  echo "  → GitHub Actions 上的构建不受影响（跑在 GitHub 服务器）"
fi
exit 1
