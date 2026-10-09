"""本地用户账号、JWT、会话键。"""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import bcrypt
import jwt
from loguru import logger

from core.security.secrets import env_alias, load_app_config, resolve_env_ref

ROLES = frozenset({"admin", "member", "readonly"})

_JWT_SECRET_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "security" / "jwt_secret"
_jwt_secret_runtime: str | None = None


@dataclass(frozen=True)
class CurrentUser:
    id: str
    username: str
    role: str
    display_name: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _read_or_create_jwt_secret() -> str:
    global _jwt_secret_runtime
    if _jwt_secret_runtime:
        return _jwt_secret_runtime
    if _JWT_SECRET_FILE.is_file():
        _jwt_secret_runtime = _JWT_SECRET_FILE.read_text(encoding="utf-8").strip()
        return _jwt_secret_runtime
    secret = secrets.token_urlsafe(32)
    _JWT_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    _JWT_SECRET_FILE.write_text(secret, encoding="utf-8")
    _jwt_secret_runtime = secret
    logger.warning(
        "已生成本地 JWT 密钥: {} — 建议设置 HUANZHEN_JWT_SECRET 或 security.jwt_secret",
        _JWT_SECRET_FILE,
    )
    return secret


def _jwt_settings() -> tuple[str, int]:
    cfg = load_app_config()
    sec = cfg.get("security") or {}
    raw = sec.get("jwt_secret") or env_alias("JWT_SECRET") or ""
    if isinstance(raw, str) and raw.startswith("${"):
        raw = resolve_env_ref(raw)
    secret = (raw or "").strip() or _read_or_create_jwt_secret()
    hours = int(sec.get("jwt_expire_hours", 72))
    return secret, hours


# bcrypt 只取密码的前 72 字节，超过会直接抛 ValueError。
# 而接口 schema 允许最长 128 字符 —— 25 个汉字就是 75 字节，很容易触发：
# 建号/改密会抛未捕获的 ValueError 变成 HTTP 500，bootstrap 甚至会崩启动。
# 这里采用通行做法（同 passlib 的 bcrypt_sha256）：超长密码先做 SHA-256 再交给
# bcrypt，存储时打上前缀标记；旧的无前缀哈希仍按原样校验，保证既有用户不受影响。
_BCRYPT_SHA256_PREFIX = "bcrypt_sha256$"


def _prehash(password: str) -> str:
    """SHA-256 + base64，把任意长度密码压到 bcrypt 可接受的 44 字节以内。"""
    import base64
    import hashlib

    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


def hash_password(password: str) -> str:
    """哈希密码。超过 bcrypt 72 字节上限时自动改用 SHA-256 预哈希。"""
    raw = password.encode("utf-8")
    if len(raw) > 72:
        return _BCRYPT_SHA256_PREFIX + bcrypt.hashpw(
            _prehash(password).encode("ascii"), bcrypt.gensalt()
        ).decode("ascii")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """校验密码，同时兼容预哈希格式与历史的无前缀 bcrypt 哈希。"""
    try:
        if password_hash.startswith(_BCRYPT_SHA256_PREFIX):
            stored = password_hash[len(_BCRYPT_SHA256_PREFIX) :]
            return bcrypt.checkpw(_prehash(password).encode("ascii"), stored.encode("ascii"))
        raw = password.encode("utf-8")
        if len(raw) > 72:
            # 历史哈希不可能由 >72 字节密码产生（当年就会抛错），直接判定失败
            return False
        return bcrypt.checkpw(raw, password_hash.encode("ascii"))
    except Exception:
        return False


def create_access_token(user_id: str, username: str, role: str) -> tuple[str, int]:
    secret, hours = _jwt_settings()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=hours)
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "exp": int(expires.timestamp()),
        "iat": int(now.timestamp()),
    }
    token = jwt.encode(payload, secret, algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode("ascii")
    return token, int(hours * 3600)


def decode_access_token(token: str) -> dict[str, Any] | None:
    secret, _ = _jwt_settings()
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def user_session_key(user_id: str, conversation_id: str) -> str:
    """nanobot 会话文件键：按用户隔离。"""
    return f"user:{user_id}:{conversation_id}"


def parse_session_conversation_id(session_key: str, user_id: str) -> str | None:
    prefix = f"user:{user_id}:"
    if session_key.startswith(prefix):
        return session_key[len(prefix) :]
    return None


def bootstrap_admin_if_needed() -> None:
    """无用户时创建首个 admin（配置 security.bootstrap_admin）。"""
    from core.database.db import get_db

    db = get_db()
    if db.count_users() > 0:
        return

    cfg = load_app_config()
    sec = cfg.get("security") or {}
    boot = sec.get("bootstrap_admin") or {}
    username = (boot.get("username") or "admin").strip()
    # 兼容两种命名：当前用 HUANZHEN_ADMIN_PASSWORD，
    # 历史部署用的是 KEJI_ADMIN_PASSWORD，config.yaml 里的 ${...} 引用可能解析为空。
    raw_pw = boot.get("password") or env_alias("ADMIN_PASSWORD") or ""
    if isinstance(raw_pw, str) and raw_pw.startswith("${"):
        raw_pw = resolve_env_ref(raw_pw)
    password = (raw_pw or "").strip()
    if not password:
        password = secrets.token_urlsafe(12)
        logger.warning(
            "已创建默认管理员 {} / 临时密码: {} — 请尽快在管理页修改密码",
            username,
            password,
        )
    display = boot.get("display_name") or "系统管理员"
    uid = db.create_user(
        username=username,
        password_hash=hash_password(password),
        role="admin",
        display_name=display,
    )
    try:
        from core.workspace import ensure_user_dir

        ensure_user_dir(uid)
    except Exception:
        pass
    logger.info("已初始化管理员账号: {} (id={})", username, uid)
