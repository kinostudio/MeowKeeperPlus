"""Password-protected, portable JSON backup for non-private settings and Telegram secrets."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from collections.abc import Mapping
from typing import Any

FORMAT = "meowkeeper-encrypted-settings"
VERSION = 1
MIN_PASSWORD_LENGTH = 6
MAX_BACKUP_BYTES = 1_000_000
_AAD = f"{FORMAT}:v{VERSION}".encode("ascii")


def _key(password: str, salt: bytes) -> bytes:
    if len(password) < MIN_PASSWORD_LENGTH or len(password) > 1024:
        raise ValueError(f"備份密碼需至少 {MIN_PASSWORD_LENGTH} 個字元，且不得超過 1024 個字元。")
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**15, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)


def encrypt_settings(payload: Mapping[str, Any], password: str) -> dict[str, Any]:
    """Encrypt settings before they leave the Host; never return plaintext secrets."""

    from Crypto.Cipher import AES

    plaintext = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(plaintext) > MAX_BACKUP_BYTES:
        raise ValueError("設定資料超過 1 MB，無法建立備份。")
    salt, nonce = os.urandom(16), os.urandom(12)
    cipher = AES.new(_key(password, salt), AES.MODE_GCM, nonce=nonce)
    cipher.update(_AAD)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    return {
        "format": FORMAT,
        "version": VERSION,
        "kdf": "scrypt-n32768-r8-p1",
        "cipher": "aes-256-gcm",
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "tag": base64.b64encode(tag).decode("ascii"),
        "data": base64.b64encode(ciphertext).decode("ascii"),
    }


def decrypt_settings(envelope: Mapping[str, Any], password: str) -> dict[str, Any]:
    """Authenticate the entire payload; report no secret-bearing error details."""

    from Crypto.Cipher import AES

    if (envelope.get("format"), envelope.get("version"), envelope.get("kdf"), envelope.get("cipher")) != (
        FORMAT, VERSION, "scrypt-n32768-r8-p1", "aes-256-gcm"
    ):
        raise ValueError("不是支援的喵喵守護者+ 加密設定備份。")
    try:
        salt = base64.b64decode(envelope["salt"], validate=True)
        nonce = base64.b64decode(envelope["nonce"], validate=True)
        tag = base64.b64decode(envelope["tag"], validate=True)
        data = base64.b64decode(envelope["data"], validate=True)
        if len(salt) != 16 or len(nonce) != 12 or len(tag) != 16 or len(data) > MAX_BACKUP_BYTES:
            raise TypeError
        cipher = AES.new(_key(password, salt), AES.MODE_GCM, nonce=nonce)
        cipher.update(_AAD)
        payload = json.loads(cipher.decrypt_and_verify(data, tag).decode("utf-8"))
        if not isinstance(payload, dict):
            raise TypeError
        return payload
    except (KeyError, TypeError, UnicodeError, ValueError, OverflowError, binascii.Error) as exc:
        raise ValueError("備份密碼錯誤、檔案遭修改或格式損毀；未匯入任何設定。") from exc
