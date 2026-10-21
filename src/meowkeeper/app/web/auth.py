"""本機 Web API 的最小應用層驗證契約。"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass


class AuthError(ValueError):
    """請求未通過 API 認證或 CSRF 檢查。"""


@dataclass(frozen=True)
class ApiAuthenticator:
    """使用 Bearer／X-MeowKeeper-Token，並以獨立 header 防止跨站修改。"""

    token: str
    csrf_token: str

    def __post_init__(self) -> None:
        if len(self.token) < 16:
            raise ValueError("Web API token 至少需要 16 個字元。")
        if len(self.csrf_token) < 16:
            raise ValueError("Web API CSRF token 至少需要 16 個字元。")

    @classmethod
    def from_environment(cls, variable: str = "MEOWKEEPER_WEB_TOKEN") -> ApiAuthenticator:
        token = os.getenv(variable, "").strip()
        if not token:
            raise AuthError(
                f"尚未設定 {variable}；請由管理者建立 Web API token，未設定時不開啟操作 API。"
            )
        return cls.from_token(token)

    @classmethod
    def from_token(cls, token: str) -> ApiAuthenticator:
        value = token.strip()
        csrf = hashlib.sha256((value + ":csrf").encode("utf-8")).hexdigest()
        return cls(token=value, csrf_token=csrf)

    @classmethod
    def generate(cls) -> ApiAuthenticator:
        """供明確的首次設定流程產生暫時 token；不會自行寫入檔案。"""

        return cls(token=secrets.token_urlsafe(32), csrf_token=secrets.token_urlsafe(32))

    def check_token(self, candidate: str | None) -> bool:
        if not candidate:
            return False
        value = candidate.strip()
        if value.lower().startswith("bearer "):
            value = value[7:].strip()
        return hmac.compare_digest(value, self.token)

    def check_csrf(self, candidate: str | None) -> bool:
        return bool(candidate) and hmac.compare_digest(candidate.strip(), self.csrf_token)

    def redact(self, value: str) -> str:
        """遮罩可能包含 token 的診斷文字。"""

        if not value:
            return value
        return value.replace(self.token, "[REDACTED]").replace(self.csrf_token, "[REDACTED]")
