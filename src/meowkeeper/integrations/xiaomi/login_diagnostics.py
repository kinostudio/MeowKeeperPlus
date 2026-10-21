"""Safe Xiaomi/go2rtc login diagnostics and conservative classification.

go2rtc v1.9.14 exposes a small ``LoginError`` structure and Xiaomi may return
provider-specific JSON codes.  This module keeps the raw response at the
capture boundary, stores only masked evidence, and deliberately avoids
interpreting ``70016`` as a bad password when Xiaomi did not provide a
credential-specific reason.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from meowkeeper.integrations.go2rtc.redaction import mask_response_body, redact_diagnostic_text


class XiaomiLoginClassification(str, Enum):
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILED = "login_failed"
    INVALID_CREDENTIALS = "invalid_credentials"
    VERIFICATION_REQUIRED = "verification_required"
    CAPTCHA_REQUIRED = "captcha_required"
    XIAOMI_RISK_CONTROL = "xiaomi_risk_control"
    UNSUPPORTED_LOGIN_FLOW = "unsupported_login_flow"
    UPSTREAM_API_CHANGED = "upstream_api_changed"
    NETWORK_ERROR = "network_error"
    UNKNOWN_ERROR = "unknown_error"


LOGIN_CLASSIFICATIONS = tuple(item.value for item in XiaomiLoginClassification)
_70016_MESSAGE = (
    "小米伺服器拒絕此密碼登入流程；帳密可能正確，但目前 go2rtc 登入方式可能受到驗證或 API 相容性限制。"
)
_GENERIC_MESSAGE = "小米登入尚未完成，請查看診斷資訊後由官方登入頁處理。"
_LOGIN_REQUEST_ACCEPTED_MESSAGE = "小米登入請求已接受；正在透過 GET 驗證帳號 session。"
_LOGIN_FAILED_MESSAGE = "小米登入失敗，請查看安全化診斷資訊後由官方登入頁處理。"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _lookup(payload: dict[str, Any], *keys: str) -> Any:
    lowered = {str(key).lower(): value for key, value in payload.items()}
    for key in keys:
        if key.lower() in lowered:
            return lowered[key.lower()]
    return None


def _parse_payload(body: object) -> dict[str, Any]:
    if isinstance(body, dict):
        return dict(body)
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    if isinstance(body, str):
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return {"description": body}
        return dict(parsed) if isinstance(parsed, dict) else {"result": parsed}
    if isinstance(body, list):
        return {"result": body}
    if body is None:
        return {}
    return {"description": str(body)}


def _account_items(body: object) -> list[Any] | None:
    """Return an account-list payload without retaining any account value."""

    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            return None
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("accounts", "users", "result"):
            value = body.get(key)
            if isinstance(value, list):
                return value
    return None


def _account_uid_present(body: object, expected_uid: str | None) -> bool | None:
    """Compare a UID in memory and expose only a presence boolean."""

    if not expected_uid:
        return None
    expected = str(expected_uid).strip()
    if not expected:
        return None
    items = _account_items(body)
    if items is None:
        return False
    for item in items:
        if isinstance(item, dict):
            candidates = (item.get("uid"), item.get("userID"), item.get("user_id"), item.get("id"))
        else:
            candidates = (item,)
        if any(str(candidate).strip() == expected for candidate in candidates if candidate is not None):
            return True
    return False


def _safe_response_body(body: object, *, phase: str, account_uid_present: bool | None) -> object:
    """Replace account-list values with counts and presence-only evidence."""

    if phase != "get":
        return body
    items = _account_items(body)
    if items is None:
        return body
    return {
        "account_count": len(items),
        "account_uid_present": account_uid_present,
    }


def _present(value: Any) -> bool:
    return value not in (None, "", False, [], {})


def safe_login_error_structure(value: object) -> dict[str, Any]:
    """Expose the shape of go2rtc LoginError without its contents."""

    if value is None:
        return {
            "type": None,
            "captcha_present": False,
            "verify_phone_present": False,
            "verify_email_present": False,
        }
    if isinstance(value, BaseException):
        return {
            "type": type(value).__name__,
            "captcha_present": _present(getattr(value, "Captcha", None)),
            "verify_phone_present": _present(getattr(value, "VerifyPhone", None)),
            "verify_email_present": _present(getattr(value, "VerifyEmail", None)),
        }
    if isinstance(value, dict):
        return {
            "type": str(value.get("type") or value.get("Type") or "LoginError"),
            "captcha_present": bool(
                value.get("captcha_present")
                or _present(value.get("Captcha") or value.get("captcha") or value.get("captchaUrl"))
            ),
            "verify_phone_present": bool(
                value.get("verify_phone_present")
                or _present(value.get("VerifyPhone") or value.get("verify_phone"))
            ),
            "verify_email_present": bool(
                value.get("verify_email_present")
                or _present(value.get("VerifyEmail") or value.get("verify_email"))
            ),
            "need_verify": bool(value.get("need_verify") or value.get("needVerify")),
        }
    return {"type": type(value).__name__, "captcha_present": False}


def _explicit_invalid(text: str, code: str) -> bool:
    patterns = (
        "invalid password",
        "wrong password",
        "invalid credentials",
        "incorrect password",
        "密碼錯誤",
        "密码错误",
        "帳號或密碼錯誤",
        "账号或密码错误",
        "用户名或密码错误",
        "username or password",
    )
    if any(pattern in text for pattern in patterns):
        return True
    return code in {"10017", "10018", "40101", "40001"} and "credential" in text


def _verification_signal(payload: dict[str, Any], text: str, login_error: dict[str, Any]) -> bool:
    direct = (
        payload.get("need_verify"),
        payload.get("needVerify"),
        payload.get("notificationUrl"),
        payload.get("notification_url"),
        payload.get("verifyPhone"),
        payload.get("verifyEmail"),
        payload.get("verify_phone"),
        payload.get("verify_email"),
        payload.get("verify_phone_present"),
        payload.get("verify_email_present"),
        payload.get("verificationRequired"),
        login_error.get("verify_phone_present"),
        login_error.get("verify_email_present"),
        login_error.get("need_verify"),
    )
    if any(_present(item) for item in direct):
        return True
    return any(
        phrase in text
        for phrase in (
            "verification required",
            "need verify",
            "verify your",
            "需要驗證",
            "需要验证",
            "請驗證",
            "请验证",
            "verification code",
            "驗證碼",
            "验证码",
        )
    )


def _captcha_signal(payload: dict[str, Any], text: str, login_error: dict[str, Any]) -> bool:
    value = _lookup(
        payload,
        "captchaUrl",
        "captchaURL",
        "captcha_url",
        "captcha",
        "captcha_present",
        "captchaPresent",
    )
    return _present(value) or bool(login_error.get("captcha_present")) or any(
        phrase in text for phrase in ("captcha", "驗證碼圖片", "验证码图片")
    )


def _risk_signal(payload: dict[str, Any], text: str) -> bool:
    security_status = _lookup(payload, "securityStatus", "security_status")
    if isinstance(security_status, str) and security_status.isdigit():
        security_status = int(security_status)
    if isinstance(security_status, (int, float)) and security_status > 0:
        return True
    return any(
        phrase in text
        for phrase in (
            "risk control",
            "risk-control",
            "blocked",
            "too many attempts",
            "风控",
            "風控",
            "帳號已被鎖",
            "账号已被锁",
            "安全策略",
            "安全策略",
        )
    )


def _unsupported_signal(text: str) -> bool:
    return any(
        phrase in text
        for phrase in (
            "unsupported",
            "not supported",
            "browser login",
            "qr login",
            "token import",
            "不支援此登入",
            "不支持此登录",
            "需要瀏覽器",
            "需要浏览器",
        )
    )


def _message_for(classification: XiaomiLoginClassification, *, code: str) -> str:
    if code == "70016":
        return _70016_MESSAGE
    return {
        XiaomiLoginClassification.LOGIN_SUCCESS: "小米登入已成功驗證，帳號 session 已存在。",
        XiaomiLoginClassification.LOGIN_FAILED: _LOGIN_FAILED_MESSAGE,
        XiaomiLoginClassification.INVALID_CREDENTIALS: "小米伺服器回報帳號或密碼不正確；請由官方登入頁確認，MeowKeeper+ 不會保存密碼。",
        XiaomiLoginClassification.CAPTCHA_REQUIRED: "小米登入需要 CAPTCHA，請在官方登入頁完成驗證後，再按明確重試。",
        XiaomiLoginClassification.VERIFICATION_REQUIRED: "小米登入需要 Email 或簡訊驗證，請完成官方驗證後，再按明確重試。",
        XiaomiLoginClassification.XIAOMI_RISK_CONTROL: "小米伺服器目前啟用安全風控，已暫停自動重試，請稍後由官方流程完成登入。",
        XiaomiLoginClassification.UNSUPPORTED_LOGIN_FLOW: "目前 go2rtc 可能不支援此小米登入流程，請使用官方瀏覽器／驗證流程，不要重複提交密碼。",
        XiaomiLoginClassification.UPSTREAM_API_CHANGED: "小米登入回應格式或 API 行為與 go2rtc 預期不同；帳密可能正確，請不要反覆重試。",
        XiaomiLoginClassification.NETWORK_ERROR: "無法連線小米登入服務，請確認網路後由使用者明確按重試。",
        XiaomiLoginClassification.UNKNOWN_ERROR: _GENERIC_MESSAGE,
    }[classification]


@dataclass(frozen=True)
class XiaomiLoginDiagnostic:
    classification: str
    code: str | None = None
    description: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    response_body_masked: str = ""
    stderr_masked: str = ""
    login_error: dict[str, Any] = field(default_factory=dict)
    captcha_present: bool = False
    verification_present: bool = False
    risk_control_signal: bool = False
    message_zh_tw: str = _GENERIC_MESSAGE
    observed_at: str = field(default_factory=_now_iso)
    phase: str = "generic"
    account_uid_present: bool | None = None

    @property
    def is_70016(self) -> bool:
        return self.code == "70016"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["classification"] = str(self.classification)
        payload["is_70016"] = self.is_70016
        return payload


def classify_login_response(
    body: object = None,
    *,
    http_status: int | None = None,
    content_type: str | None = None,
    stderr: object = "",
    login_error: object = None,
    network_error: object = None,
    phase: str = "generic",
    expected_uid: str | None = None,
) -> XiaomiLoginDiagnostic:
    """Classify a captured Xiaomi response without ever returning raw secrets.

    ``phase="generic"`` preserves the historical conservative diagnostic
    categories.  Use ``phase="post"`` for the form POST to ``/api/xiaomi``
    and ``phase="get"`` for the subsequent account-list verification.  The
    expected UID is compared in memory and never included in the diagnostic.
    """

    if phase not in {"generic", "post", "get"}:
        raise ValueError("Xiaomi 登入 phase 必須是 generic、post 或 get。")
    # A successful GET account-list response is unambiguous even for legacy
    # callers that have not yet supplied an explicit phase.  This prevents a
    # JSON account array from falling through to ``unknown_error``.
    if phase == "generic" and http_status == 200 and _account_items(body) is not None:
        phase = "get"

    payload = _parse_payload(body)
    safe_login_error = safe_login_error_structure(login_error)
    code_value = _lookup(payload, "code", "errorCode", "error_code")
    code = str(code_value) if code_value not in (None, "") else None
    description_value = _lookup(payload, "description", "message", "error", "detail")
    description = (
        redact_diagnostic_text(_text(description_value), limit=240)
        if description_value not in (None, "")
        else None
    )
    text = " ".join(
        _text(item).lower()
        for item in (
            code,
            description,
            _lookup(payload, "result"),
            _lookup(payload, "securityStatus", "security_status"),
        )
        if item not in (None, "")
    )
    text = redact_diagnostic_text(text, limit=1000).lower()
    challenge_captcha = _captcha_signal(payload, text, safe_login_error)
    challenge_verification = _verification_signal(payload, text, safe_login_error)
    risk_signal = _risk_signal(payload, text)

    account_uid_present = _account_uid_present(body, expected_uid) if phase == "get" else None

    if network_error is not None:
        classification = XiaomiLoginClassification.NETWORK_ERROR
    elif _explicit_invalid(text, code or ""):
        classification = XiaomiLoginClassification.INVALID_CREDENTIALS
    elif challenge_captcha:
        classification = XiaomiLoginClassification.CAPTCHA_REQUIRED
    elif challenge_verification:
        classification = XiaomiLoginClassification.VERIFICATION_REQUIRED
    elif risk_signal:
        classification = XiaomiLoginClassification.XIAOMI_RISK_CONTROL
    elif code == "70016":
        # Xiaomi supplied no captcha/challenge details.  This is an upstream
        # contract/compatibility failure, not evidence that the password is bad.
        classification = XiaomiLoginClassification.UPSTREAM_API_CHANGED
    elif _unsupported_signal(text) or http_status == 401:
        # A bare 401 is intentionally not treated as a bad password.  The
        # official browser/challenge flow may have been rejected upstream.
        classification = XiaomiLoginClassification.UNSUPPORTED_LOGIN_FLOW
    elif (
        str(_lookup(payload, "result")).lower() == "error"
        and _lookup(payload, "location", "passToken", "ssecurity") in (None, "")
    ) or http_status is not None and http_status >= 500:
        classification = XiaomiLoginClassification.UPSTREAM_API_CHANGED
    elif body in (None, "", b"") and http_status is None:
        classification = XiaomiLoginClassification.UNKNOWN_ERROR
    else:
        classification = XiaomiLoginClassification.UNKNOWN_ERROR

    # Login flow rules are intentionally applied after challenge detection so
    # HTTP 401 CAPTCHA/verification responses retain their specific category.
    if phase == "post" and http_status == 200 and body in (None, "", b""):
        classification = XiaomiLoginClassification.LOGIN_SUCCESS
        message_override = _LOGIN_REQUEST_ACCEPTED_MESSAGE
    elif phase == "get" and http_status == 200:
        if account_uid_present is True or (
            account_uid_present is None and bool(_account_items(body))
        ):
            classification = XiaomiLoginClassification.LOGIN_SUCCESS
        else:
            classification = XiaomiLoginClassification.LOGIN_FAILED
        message_override = None
    elif (
        phase in {"post", "get"}
        and (
            (http_status is not None and http_status >= 500)
            or (http_status == 401 and not (challenge_captcha or challenge_verification))
        )
        or (
            phase == "post"
            and http_status == 200
            and classification
            in {
                XiaomiLoginClassification.UNKNOWN_ERROR,
                XiaomiLoginClassification.UPSTREAM_API_CHANGED,
                XiaomiLoginClassification.UNSUPPORTED_LOGIN_FLOW,
            }
        )
    ):
        classification = XiaomiLoginClassification.LOGIN_FAILED
        message_override = None
    else:
        message_override = None

    message = _message_for(classification, code=code or "")
    if message_override:
        message = message_override
    if network_error is not None:
        message = _message_for(XiaomiLoginClassification.NETWORK_ERROR, code=code or "")
    safe_body = _safe_response_body(body, phase=phase, account_uid_present=account_uid_present)
    return XiaomiLoginDiagnostic(
        classification=classification.value,
        code=code,
        description=description,
        http_status=http_status,
        content_type=content_type,
        response_body_masked=mask_response_body(safe_body),
        stderr_masked=redact_diagnostic_text(stderr),
        login_error=safe_login_error,
        captcha_present=challenge_captcha,
        verification_present=challenge_verification,
        risk_control_signal=risk_signal,
        message_zh_tw=message,
        phase=phase,
        account_uid_present=account_uid_present,
    )


def classify_login_flow_response(
    body: object = None,
    *,
    phase: str,
    expected_uid: str | None = None,
    http_status: int | None = None,
    content_type: str | None = None,
    stderr: object = "",
    login_error: object = None,
    network_error: object = None,
) -> XiaomiLoginDiagnostic:
    """Explicit two-step Xiaomi login classifier.

    This wrapper makes it difficult for callers to accidentally interpret the
    POST acknowledgement and GET account verification as one response.
    """

    if phase not in {"post", "get"}:
        raise ValueError("Xiaomi 登入 flow phase 必須是 post 或 get。")
    return classify_login_response(
        body,
        http_status=http_status,
        content_type=content_type,
        stderr=stderr,
        login_error=login_error,
        network_error=network_error,
        phase=phase,
        expected_uid=expected_uid,
    )


__all__ = [
    "LOGIN_CLASSIFICATIONS",
    "XiaomiLoginClassification",
    "XiaomiLoginDiagnostic",
    "classify_login_flow_response",
    "classify_login_response",
    "safe_login_error_structure",
]
