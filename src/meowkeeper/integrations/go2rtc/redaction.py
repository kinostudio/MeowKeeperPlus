"""Secret-safe diagnostic text handling for external go2rtc output.

The go2rtc process is an external dependency.  Its errors can contain
provider URLs, cookies or login fields, so diagnostics must be reduced before
they are persisted or returned through the Web API.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

_URL_SECRET = re.compile(r"(rtsp://[^:/\s]+:)[^@/\s]+(@)", re.IGNORECASE)
_KEY_VALUE = re.compile(
    r"(?i)(password|passwd|token|secret|captcha|verification[_-]?code|username|account|email|phone)"
    r"(\s*[=:]\s*)[^\s,;&]+"
)
_JSON_SECRET = re.compile(
    r'(?i)("(?:password|passwd|token|secret|captcha|verification[_-]?code|'
    r'passToken|serviceToken|ssecurity|location|notificationUrl|username|account|email|phone)"\s*:\s*)"[^"]*"'
)
_URL = re.compile(r"(?i)(?:https?|rtsp|xiaomi)://[^\s\"'<>]+")
_MAX_DIAGNOSTIC_CHARS = 4096

_SECRET_KEYS = {
    "password",
    "passwd",
    "token",
    "secret",
    "captchacode",
    "verificationcode",
    "passtoken",
    "servicetoken",
    "ssecurity",
    "location",
    "notificationurl",
    "username",
    "account",
}


def redact_diagnostic_text(value: object, *, limit: int = _MAX_DIAGNOSTIC_CHARS) -> str:
    """Return bounded text suitable for logs/API responses.

    This intentionally removes complete URLs, not only URL credentials.  A
    Xiaomi ``location`` URL can carry an account token in its query string.
    """

    text = str(value or "")
    text = _URL.sub("<url redacted>", text)
    text = redact_secrets(text)
    text = _JSON_SECRET.sub(r'\1"<redacted>"', text)
    if len(text) > limit:
        return text[: max(0, limit - 15)] + "…<truncated>"
    return text


def _safe_body_value(key: str, value: Any) -> Any:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    if normalized in _SECRET_KEYS:
        return "<redacted>" if value not in (None, "", False) else value
    if normalized in {"captchaurl", "captcha"}:
        return "<present>" if value not in (None, "", False) else None
    if normalized in {"email", "phone", "phonenumber"} and value not in (None, ""):
        return "<redacted>"
    if isinstance(value, Mapping):
        return {str(child_key): _safe_body_value(str(child_key), child_value) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [_safe_body_value(key, child) for child in value]
    if isinstance(value, str):
        return redact_diagnostic_text(value, limit=512)
    return value


def mask_response_body(value: object, *, limit: int = _MAX_DIAGNOSTIC_CHARS) -> str:
    """Mask a JSON or text response without retaining login secrets."""

    if value is None:
        return ""
    if isinstance(value, (Mapping, list)):
        safe = _safe_body_value("root", value)
        return redact_diagnostic_text(json.dumps(safe, ensure_ascii=False, separators=(",", ":")), limit=limit)
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    try:
        parsed = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return redact_diagnostic_text(text, limit=limit)
    safe = _safe_body_value("root", parsed)
    return redact_diagnostic_text(json.dumps(safe, ensure_ascii=False, separators=(",", ":")), limit=limit)


def redact_secrets(value: str) -> str:
    value = _URL_SECRET.sub(r"\1<redacted>\2", value)
    return _KEY_VALUE.sub(r"\1\2<redacted>", value)


__all__ = ["mask_response_body", "redact_diagnostic_text", "redact_secrets"]
