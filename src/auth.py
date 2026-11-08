# -*- coding: utf-8 -*-
"""演示版登录 / JWT / 用户目录。

- 账号放 data/users.json（首次启动自动生成演示账号；PBKDF2 存哈希）
- JWT 用标准库实现 HS256，secret 落 data/.jwt_secret
- 默认：所有 /api/* 需要 Bearer；眼镜端只读/写入的几条接口允许不带 token（走最近登录的用户）
- AR_AUTH_DISABLED=1：整个鉴权关掉，直接单用户跑（给旧测试用）
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA = os.environ.get("AR_DATA_DIR") or os.path.join(ROOT, "data")
USERS_PATH = os.path.join(DATA, "users.json")
SECRET_PATH = os.path.join(DATA, ".jwt_secret")
DISABLED = os.environ.get("AR_AUTH_DISABLED", "") in ("1", "true", "yes")
TOKEN_TTL = int(os.environ.get("AR_TOKEN_TTL", "43200"))   # 12h

DEFAULT_USERS = {
    "demo": {"password": "demo123", "name": "演示用户"},
    "guest": {"password": "guest123", "name": "访客"},
    "wjh": {"password": "wjh123", "name": "王建华"},
}

_LOCK = threading.RLock()
_SECRET = None
_ACTIVE = "demo"


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _pbkdf2(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)


def _load_users() -> dict:
    with _LOCK:
        if not os.path.isfile(USERS_PATH):
            out = {}
            for uid, info in DEFAULT_USERS.items():
                salt = secrets.token_bytes(16)
                out[uid] = {
                    "name": info["name"],
                    "salt": salt.hex(),
                    "hash": _pbkdf2(info["password"], salt).hex(),
                }
            os.makedirs(os.path.dirname(USERS_PATH), exist_ok=True)
            with open(USERS_PATH, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=1)
            return out
        with open(USERS_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}


def list_users() -> list:
    users = _load_users()
    return [{"username": k, "name": (v or {}).get("name") or k} for k in users]


def verify_password(username: str, password: str) -> bool:
    users = _load_users()
    u = users.get((username or "").strip())
    if not u:
        return False
    salt = bytes.fromhex(u.get("salt") or "")
    return hmac.compare_digest(_pbkdf2(password or "", salt).hex(), u.get("hash") or "")


def secret() -> bytes:
    global _SECRET
    with _LOCK:
        if _SECRET is None:
            try:
                with open(SECRET_PATH, "rb") as f:
                    _SECRET = f.read().strip()
            except OSError:
                _SECRET = b""
            if len(_SECRET) < 32:
                _SECRET = secrets.token_bytes(32)
                os.makedirs(os.path.dirname(SECRET_PATH), exist_ok=True)
                with open(SECRET_PATH, "wb") as f:
                    f.write(_SECRET)
        return _SECRET


def make_token(username: str, ttl: int = TOKEN_TTL) -> str:
    header = _b64e(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64e(json.dumps({
        "sub": username,
        "iat": int(time.time()),
        "exp": int(time.time()) + int(ttl or TOKEN_TTL),
    }, separators=(",", ":")).encode())
    sig = _b64e(hmac.new(secret(), (header + "." + payload).encode(), hashlib.sha256).digest())
    return header + "." + payload + "." + sig


def verify_token(token: str) -> str:
    try:
        header, payload, sig = (token or "").split(".")
        want = _b64e(hmac.new(secret(), (header + "." + payload).encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, want):
            return ""
        data = json.loads(_b64d(payload))
        if int(data.get("exp") or 0) < int(time.time()):
            return ""
        sub = str(data.get("sub") or "").strip()
        return sub if sub in _load_users() else ""
    except Exception:  # noqa: BLE001 - 坏 token 一律当未登录
        return ""


def login(username: str, password: str) -> dict:
    global _ACTIVE
    username = (username or "").strip()
    if not username or not verify_password(username, password):
        raise ValueError("用户名或密码不正确")
    if not DISABLED:
        _ACTIVE = username
    users = _load_users()
    return {
        "token": make_token(username),
        "user": {"username": username, "name": (users.get(username) or {}).get("name") or username},
    }


def active_user() -> str:
    return _ACTIVE


def set_active(username: str) -> None:
    global _ACTIVE
    if username:
        _ACTIVE = username


def display_name(username: str) -> str:
    users = _load_users()
    return (users.get(username) or {}).get("name") or username
