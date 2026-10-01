"""认证原语：口令哈希（stdlib PBKDF2）、TOTP（RFC6238）、签名 Cookie、Flash。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from typing import Any

from .config import get_settings

PBKDF2_ROUNDS = 200_000
ALGO = "pbkdf2_sha256"


# ------------------------------------------------------------------ 口令
def hash_password(password: str, rounds: int = PBKDF2_ROUNDS) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), rounds)
    return f"{ALGO}${rounds}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds_s, salt, digest = stored.split("$")
        if algo != ALGO:
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds_s))
        return hmac.compare_digest(dk.hex(), digest)
    except Exception:
        return False


def password_problem(password: str) -> str | None:
    if len(password or "") < 8:
        return "密码至少 8 位"
    if password.isdigit() or password.isalpha():
        return "密码需同时包含字母和数字"
    return None


# ------------------------------------------------------------------ TOTP
def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _totp_at(secret: str, counter: int, digits: int = 6) -> str:
    pad = "=" * (-len(secret) % 8)
    key = base64.b32decode(secret.upper() + pad)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**digits)).zfill(digits)


def totp_now(secret: str, step: int = 30, digits: int = 6) -> str:
    return _totp_at(secret, int(time.time()) // step, digits)


def verify_totp(secret: str, code: str, window: int = 1, step: int = 30, digits: int = 6) -> bool:
    if not secret or not code:
        return False
    code = code.strip().replace(" ", "")
    counter = int(time.time()) // step
    for offset in range(-window, window + 1):
        if hmac.compare_digest(_totp_at(secret, counter + offset, digits), code):
            return True
    return False


def totp_uri(secret: str, username: str) -> str:
    from urllib.parse import quote

    issuer = quote(get_settings().app_name)
    return f"otpauth://totp/{issuer}:{quote(username)}?secret={secret}&issuer={issuer}&period=30&digits=6"


# ------------------------------------------------------------- 签名 Cookie
def _sign(payload: bytes) -> str:
    key = get_settings().app_secret_key.encode()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def dumps(data: dict[str, Any]) -> str:
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"{body}.{_sign(payload)}"


def loads(token: str | None) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    try:
        payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except Exception:
        return None
    if not hmac.compare_digest(_sign(payload), sig):
        return None
    try:
        data = json.loads(payload)
    except Exception:
        return None
    if "exp" in data and float(data["exp"]) < time.time():
        return None
    return data


def make_session(user_id: int, session_version: int, must_change: bool = False) -> str:
    s = get_settings()
    return dumps(
        {
            "uid": user_id,
            "sv": session_version,
            "mcp": 1 if must_change else 0,
            "exp": time.time() + s.session_max_age,
        }
    )


def make_flash(level: str, message: str) -> str:
    return dumps({"lvl": level, "msg": message, "exp": time.time() + 60})
