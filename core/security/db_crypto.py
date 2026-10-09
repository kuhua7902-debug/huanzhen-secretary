"""外部数据库配置密码的加解密（基于机器级派生密钥）。

历史背景：产品早期名为「科吉 / Keji」，加密盐与机器名兜底值都带 keji 前缀。
为保证既有部署里已加密的数据库密码仍能解开，``decrypt_password`` 在用新密钥
失败后会回退到历史密钥；而新写入一律使用新密钥，从而逐步完成迁移。

因此本文件里的 ``_LEGACY_*`` 常量是**不可删除的迁移兼容项**，而不是命名遗留。
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# 当前密钥参数
_SALT = b"huanzhen-db-pwd"
_MACHINE_FALLBACK = "huanzhen"

# 迁移兼容：历史（Keji 时期）的盐与机器名兜底值，仅用于解开旧密文，勿删勿改。
_LEGACY_SALT = b"keji-db-pwd"
_LEGACY_MACHINE_FALLBACK = "keji"


def _cipher(salt: bytes, machine_fallback: str) -> Fernet:
    machine_id = hashlib.md5(
        os.environ.get("COMPUTERNAME", machine_fallback).encode()
    ).hexdigest()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100000
    )
    key = base64.urlsafe_b64encode(kdf.derive(machine_id.encode()))
    return Fernet(key)


def encrypt_password(password: str) -> str:
    """用当前密钥加密密码；失败返回空串（与历史实现保持一致）。"""
    try:
        return _cipher(_SALT, _MACHINE_FALLBACK).encrypt(password.encode()).decode()
    except Exception:
        return ""


def decrypt_password(encrypted: str) -> str:
    """解密密码；先试新密钥，再回退历史密钥，全部失败则原样返回。"""
    if not encrypted:
        return ""
    for salt, fallback in (
        (_SALT, _MACHINE_FALLBACK),
        (_LEGACY_SALT, _LEGACY_MACHINE_FALLBACK),
    ):
        try:
            return _cipher(salt, fallback).decrypt(encrypted.encode()).decode()
        except Exception:
            continue
    return encrypted
