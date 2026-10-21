"""小米 bridge 的秘密遮罩與設定檢查。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_SECRET = re.compile(r"(?:password|passwd|token|auth|verification|captcha|code|key|secret)", re.IGNORECASE)


def redact(value: str) -> str:
    value = re.sub(r"(https?://[^\s/@:]+):([^\s/@]+)@", r"\1:[REDACTED]@", value)
    return value


def public_device_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in payload.items() if not _SECRET.search(str(key))}


def reject_secret_fields(payload: Mapping[str, Any]) -> None:
    bad = [str(key) for key in payload if _SECRET.search(str(key))]
    if bad:
        raise ValueError("Xiaomi 帳號或驗證資料必須由官方流程處理，不可送入一般設定 API。")
