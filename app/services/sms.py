"""短信验证码：生成/哈希/校验/限流 + 服务商 Provider（后台可切换）。

服务商与密钥存 app_settings（后台「系统」页配置），**每次发送现取配置**——
后台切换服务商 / 改密钥即时生效，无需重启。

安全口径（见开发报告 §3.2）：
- 验证码只存 HMAC-SHA256 哈希（密钥 = APP_SECRET_KEY，随机盐前置），不存明文；
- 与 user_id 绑定（A 的码不能登 B）；一次性；5 分钟有效；错 3 次作废；
- 发送前置条件：账号密码已通过（由调用方 auth 保证）——天然防轰炸与用户名枚举；
- 三层限流：单账号 / 单 IP / 全局日上限（保险丝，防异常烧钱）。

Provider 全部纯标准库（urllib）实现，不新增依赖。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import SmsCode, User, now_iso
from . import settings as settings_svc

log = logging.getLogger("crm.sms")

PROVIDER_LABELS = {
    "console": "调试模式（验证码写运行日志，不实际发送）",
    "zhenzi": "榛子云",
    "tencent": "腾讯云短信",
    "aliyun": "阿里云短信",
}

# 密钥类设置键：后台脱敏回显；备份打包时从 app_settings 剔除（backup.py 调用）
SECRET_SETTING_KEYS = (
    "sms.zhenzi_secret",
    "sms.tencent_skey",
    "sms.aliyun_sk",
)

_FMT = "%Y-%m-%d %H:%M:%S"


# ------------------------------------------------------------------ 基础
def sms_active(db: Session) -> bool:
    """短信验证功能是否开启（总开关）。"""
    return settings_svc.get_sms_config(db)["enabled"]


def mask_secret(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return ""
    return "****" + v[-4:] if len(v) > 4 else "****"


def mask_mobile(mobile: str) -> str:
    m = (mobile or "").strip()
    if len(m) == 11:
        return f"{m[:3]}****{m[-4:]}"
    return m[:2] + "****" if len(m) > 4 else "****"


def valid_mobile(mobile: str) -> bool:
    m = (mobile or "").strip()
    return len(m) == 11 and m.startswith("1") and m.isdigit()


def _generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _code_digest(salt: str, code: str) -> str:
    key = get_settings().app_secret_key.encode()
    return hmac.new(key, f"sms|{salt}|{code}".encode(), hashlib.sha256).hexdigest()


def _code_hash(code: str) -> str:
    salt = secrets.token_hex(8)
    return f"{salt}:{_code_digest(salt, code)}"


def _hash_matches(code_hash: str, code: str) -> bool:
    salt, _, digest = (code_hash or "").partition(":")
    if not salt or not digest:
        return False
    return hmac.compare_digest(_code_digest(salt, code), digest)


# ------------------------------------------------------------------ 发放与校验
def issue_code(db: Session, user: User, ip: str) -> tuple[bool, str]:
    """给已通过密码校验的 user 发一条登录验证码。

    前置条件（由调用方保证）：密码（及 TOTP，若启用）已验证通过。
    返回 (ok, err_message)。失败原因面向最终用户，措辞不含内部细节。
    """
    cfg = settings_svc.get_sms_config(db)
    mobile = (user.mobile or "").strip()
    if not valid_mobile(mobile):
        return False, "该账号未绑定有效手机号，请联系管理员"

    now = datetime.now()
    now_s = now.strftime(_FMT)
    today_s = now.strftime("%Y-%m-%d")

    # 顺带清理：该用户已过期/已使用的旧验证码
    db.execute(
        delete(SmsCode).where(
            SmsCode.user_id == user.id,
            (SmsCode.expires_at < now_s) | (SmsCode.used_at.isnot(None)),
        )
    )

    # 冷却：同账号两条码最小间隔
    last = db.scalars(
        select(SmsCode)
        .where(SmsCode.user_id == user.id, SmsCode.scene == "login")
        .order_by(SmsCode.id.desc())
        .limit(1)
    ).first()
    if last is not None:
        try:
            last_dt = datetime.strptime(last.created_at, _FMT)
        except ValueError:
            last_dt = None
        if last_dt is not None:
            elapsed = (now - last_dt).total_seconds()
            if elapsed < cfg["send_cooldown"]:
                return False, f"发送太频繁，请 {int(cfg['send_cooldown'] - elapsed) + 1} 秒后再试"

    # 三层日限流
    n_user = db.scalar(
        select(func.count()).select_from(SmsCode).where(
            SmsCode.user_id == user.id, SmsCode.created_at >= today_s
        )
    ) or 0
    if n_user >= cfg["daily_limit_per_user"]:
        return False, "今日该账号验证码发送次数已达上限，请明日再试或使用内网登录"
    n_ip = db.scalar(
        select(func.count()).select_from(SmsCode).where(
            SmsCode.created_ip == ip, SmsCode.created_at >= today_s
        )
    ) or 0
    if n_ip >= cfg["daily_limit_per_ip"]:
        return False, "今日该网络验证码发送次数已达上限，请明日再试或使用内网登录"
    n_all = db.scalar(
        select(func.count()).select_from(SmsCode).where(SmsCode.created_at >= today_s)
    ) or 0
    if n_all >= cfg["daily_cap_total"]:
        log.error("短信全局日上限已触发（%s 条），本日停发", n_all)
        return False, "系统短信发送额度已用尽，请联系管理员"

    code = _generate_code()
    row = SmsCode(
        user_id=user.id,
        mobile=mobile,
        code_hash=_code_hash(code),
        scene="login",
        attempts=0,
        expires_at=(now + timedelta(seconds=cfg["code_ttl_seconds"])).strftime(_FMT),
        created_ip=ip,
        created_at=now_s,
    )
    db.add(row)
    db.flush()

    ok, err = _send(cfg["provider"], cfg["creds"], mobile, code)
    if not ok:
        db.delete(row)  # 发送失败不保留（不占用冷却与限额）
        db.flush()
        log.warning("短信发送失败 user=%s mobile=%s err=%s", user.username, mask_mobile(mobile), err)
        return False, err or "短信发送失败，请稍后重试"

    log.warning("短信验证码已发送 user=%s mobile=%s ip=%s", user.username, mask_mobile(mobile), ip)
    return True, ""


def verify_code(db: Session, user: User, code: str) -> tuple[bool, str]:
    """校验登录短信验证码（一次性；错 3 次作废）。调用方负责把失败计入登录锁定。"""
    now_s = now_iso()
    row = db.scalars(
        select(SmsCode)
        .where(
            SmsCode.user_id == user.id,
            SmsCode.scene == "login",
            SmsCode.used_at.is_(None),
            SmsCode.expires_at > now_s,
            SmsCode.attempts < 3,
        )
        .order_by(SmsCode.id.desc())
        .limit(1)
    ).first()
    if row is None:
        return False, "验证码不存在或已过期，请重新获取"
    code = (code or "").strip()
    if not code.isdigit() or len(code) != 6:
        return False, "验证码应为 6 位数字"
    if not _hash_matches(row.code_hash, code):
        row.attempts = int(row.attempts or 0) + 1
        db.flush()
        return False, "验证码不正确" if row.attempts < 3 else "验证码错误次数过多已作废，请重新获取"
    row.used_at = now_s
    db.flush()
    return True, ""


def cleanup_expired(db: Session) -> int:
    """删除已过期超过 1 天的验证码（每日定时任务调用）。"""
    cutoff = (datetime.now() - timedelta(days=1)).strftime(_FMT)
    result = db.execute(delete(SmsCode).where(SmsCode.expires_at < cutoff))
    return int(result.rowcount or 0)


def send_test(db: Session, mobile: str) -> tuple[bool, str]:
    """后台「发送测试短信」：用当前配置给任意手机号发一条测试码（不落库）。"""
    cfg = settings_svc.get_sms_config(db)
    code = _generate_code()
    ok, err = _send(cfg["provider"], cfg["creds"], mobile, code)
    if ok:
        log.warning("【测试短信】provider=%s mobile=%s code=%s", cfg["provider"], mask_mobile(mobile), code)
    return ok, err


# ------------------------------------------------------------------ Providers
def _send(provider: str, creds: dict, mobile: str, code: str) -> tuple[bool, str]:
    try:
        if provider == "zhenzi":
            return _send_zhenzi(creds, mobile, code)
        if provider == "tencent":
            return _send_tencent(creds, mobile, code)
        if provider == "aliyun":
            return _send_aliyun(creds, mobile, code)
        return _send_console(mobile, code)
    except Exception as exc:  # 网络/解析异常统一兜底，不让登录路径 500
        log.exception("短信发送异常 provider=%s", provider)
        return False, f"短信服务异常（{type(exc).__name__}），请稍后重试"


def _send_console(mobile: str, code: str) -> tuple[bool, str]:
    """调试模式：验证码写运行日志（docker logs hvaccrm 可见），不实际发送、零成本。"""
    log.warning("【短信·调试模式】验证码 %s（手机 %s，5 分钟内有效）", code, mask_mobile(mobile))
    return True, ""


def _http_post_json(url: str, data: bytes, headers: dict[str, str] | None = None) -> dict:
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded", **(headers or {})})
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _send_zhenzi(creds: dict, mobile: str, code: str) -> tuple[bool, str]:
    appid = creds.get("zhenzi_appid") or ""
    secret = creds.get("zhenzi_secret") or ""
    if not appid or not secret:
        return False, "榛子云未配置 appId / appSecret"
    tpl = creds.get("zhenzi_tpl") or ""
    if tpl:
        params = {
            "appId": appid, "appSecret": secret, "phone": mobile,
            "templateId": tpl, "templateParams": json.dumps([code], ensure_ascii=False),
        }
    else:
        params = {
            "appId": appid, "appSecret": secret, "phone": mobile,
            "message": f"【HVAccRM】您的验证码是{code}，5分钟内有效。",
        }
    data = urllib.parse.urlencode(params).encode("utf-8")
    payload = _http_post_json("https://sms.zhenzibj.com/sms/send.do", data)
    if str(payload.get("code")) == "0":
        return True, ""
    return False, f"榛子云返回错误：{payload.get('message') or payload}"


def _send_tencent(creds: dict, mobile: str, code: str) -> tuple[bool, str]:
    """腾讯云短信 SendSms（TC3-HMAC-SHA256 签名，纯标准库实现）。"""
    sid = creds.get("tencent_sid") or ""
    skey = creds.get("tencent_skey") or ""
    appid = creds.get("tencent_appid") or ""
    sign = creds.get("tencent_sign") or ""
    tpl = creds.get("tencent_tpl") or ""
    if not all([sid, skey, appid, sign, tpl]):
        return False, "腾讯云短信配置不完整（需 SecretId / SecretKey / SdkAppId / 签名 / 模板ID）"

    service, host = "sms", "sms.tencentcloudapi.com"
    action, version, region = "SendSms", "2021-01-11", "ap-guangzhou"
    body = json.dumps({
        "PhoneNumberSet": [f"+86{mobile}"],
        "SmsSdkAppId": str(appid),
        "SignName": sign,
        "TemplateId": str(tpl),
        "TemplateParamSet": [code],
    })
    ts = int(time.time())
    date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    ct = "application/json; charset=utf-8"
    hashed = hashlib.sha256(body.encode("utf-8")).hexdigest()
    canonical = f"POST\n/\n\ncontent-type:{ct}\nhost:{host}\n\ncontent-type;host\n{hashed}"
    scope = f"{date}/{service}/tc3_request"
    string_to_sign = f"TC3-HMAC-SHA256\n{ts}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"

    def _h(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    secret_date = _h(("TC3" + skey).encode(), date)
    secret_service = _h(secret_date, service)
    secret_signing = _h(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()
    auth = (
        f"TC3-HMAC-SHA256 Credential={sid}/{scope}, "
        f"SignedHeaders=content-type;host, Signature={signature}"
    )
    payload = _http_post_json(
        f"https://{host}/",
        body.encode("utf-8"),
        headers={"Content-Type": ct, "X-TC-Action": action, "X-TC-Timestamp": str(ts),
                 "X-TC-Version": version, "X-TC-Region": region, "Authorization": auth},
    )
    status = ((payload.get("Response") or {}).get("SendStatusSet") or [{}])[0]
    if status.get("Code") == "Ok":
        return True, ""
    err = payload.get("Response", {}).get("Error", {}).get("Message") or status.get("Message") or str(payload)
    return False, f"腾讯云返回错误：{err}"


def _penc(value: str) -> str:
    """阿里云 RPC 签名用的 RFC3986 百分号编码。"""
    return urllib.parse.quote(value, safe="-_.~")


def _send_aliyun(creds: dict, mobile: str, code: str) -> tuple[bool, str]:
    """阿里云短信 SendSms（RPC 风格 + HMAC-SHA1 签名，纯标准库实现）。"""
    ak = creds.get("aliyun_ak") or ""
    sk = creds.get("aliyun_sk") or ""
    sign = creds.get("aliyun_sign") or ""
    tpl = creds.get("aliyun_tpl") or ""
    if not all([ak, sk, sign, tpl]):
        return False, "阿里云短信配置不完整（需 AccessKeyId / AccessKeySecret / 签名 / 模板Code）"

    params = {
        "AccessKeyId": ak,
        "Action": "SendSms",
        "Format": "JSON",
        "PhoneNumbers": mobile,
        "RegionId": "cn-hangzhou",
        "SignName": sign,
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": secrets.token_hex(16),
        "SignatureVersion": "1.0",
        "TemplateCode": tpl,
        "TemplateParam": json.dumps({"code": code}),
        "Timestamp": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Version": "2017-05-25",
    }
    sorted_qs = "&".join(f"{_penc(k)}={_penc(v)}" for k, v in sorted(params.items()))
    string_to_sign = "GET&%2F&" + _penc(sorted_qs)
    sig = base64.b64encode(
        hmac.new((sk + "&").encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1).digest()
    ).decode()
    url = f"https://dysmsapi.aliyun.com/?Signature={_penc(sig)}&{sorted_qs}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=12) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("Code") == "OK":
        return True, ""
    return False, f"阿里云返回错误：{payload.get('Message') or payload}"
