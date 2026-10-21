"""不依賴外部套件的管理者密碼／Session 契約。"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path


class SessionAuthError(ValueError):
    """登入或 Session 驗證失敗。"""


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    csrf_token: str
    username: str
    expires_at: float


class SessionManager:
    """記憶體 Session store；正式部署可由受保護的 Windows secret reference 初始化。"""

    def __init__(
        self,
        *,
        timeout_seconds: int = 12 * 60 * 60,
        max_failures: int = 5,
        lockout_seconds: int = 60,
        storage_path: str | Path | None = None,
    ) -> None:
        if timeout_seconds < 60 or max_failures < 1 or lockout_seconds < 1:
            raise ValueError("Session 安全參數不正確。")
        self.timeout_seconds = timeout_seconds
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self._username: str | None = None
        self._password_hash: str | None = None
        self._setup_csrf: str | None = None
        self._storage_path = Path(storage_path).resolve() if storage_path else None
        self._failures: dict[str, list[float]] = {}
        self._sessions: dict[str, SessionRecord] = {}
        self._load_admin()

    @property
    def configured(self) -> bool:
        return self._username is not None and self._password_hash is not None

    def configure_admin(self, username: str, password: str) -> None:
        user = username.strip()
        if len(user) < 3 or len(user) > 80 or len(password) < 12:
            raise SessionAuthError("管理者帳號至少 3 個字元，密碼至少 12 個字元。")
        self._username = user
        self._password_hash = self._hash_password(password)
        self._failures.clear()
        self._setup_csrf = None
        self._save_admin()

    def begin_setup(self) -> str:
        """建立一次性的首次設定 CSRF；管理員存在時拒絕重設。"""

        if self.configured:
            raise SessionAuthError("管理者已存在，不能再次初始化。")
        if self._setup_csrf is None:
            self._setup_csrf = secrets.token_urlsafe(32)
        return self._setup_csrf

    def complete_setup(self, username: str, password: str, csrf_token: str | None) -> None:
        if self.configured:
            raise SessionAuthError("管理者已存在，不能再次初始化。")
        expected = self.begin_setup()
        if not csrf_token or not hmac.compare_digest(str(csrf_token), expected):
            raise SessionAuthError("首次設定的 CSRF 驗證失敗。")
        self.configure_admin(username, password)

    def _load_admin(self) -> None:
        if self._storage_path is None or not self._storage_path.is_file():
            return
        try:
            payload = json.loads(self._storage_path.read_text(encoding="utf-8"))
            username = payload.get("username")
            password_hash = payload.get("password_hash")
            if isinstance(username, str) and isinstance(password_hash, str) and username and password_hash:
                self._username = username
                self._password_hash = password_hash
        except (OSError, ValueError, TypeError):
            # 損壞的設定不應讓 localhost health endpoint 無法啟動；使用者可重新
            # 移除損壞檔案後走 setup 流程。
            self._username = None
            self._password_hash = None

    def _save_admin(self) -> None:
        if self._storage_path is None or not self._username or not self._password_hash:
            return
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._storage_path.with_suffix(self._storage_path.suffix + ".tmp")
            temporary.write_text(
                json.dumps({"username": self._username, "password_hash": self._password_hash}, ensure_ascii=False),
                encoding="utf-8",
            )
            temporary.replace(self._storage_path)
        except OSError:
            # 記憶體中的管理員仍有效；Host 會在診斷頁回報持久化不可用。
            return

    def _hash_password(self, password: str, *, salt: bytes | None = None) -> str:
        salt_value = salt or secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_value, 210_000)
        return f"pbkdf2_sha256$210000${salt_value.hex()}${digest.hex()}"

    def _verify_password(self, password: str) -> bool:
        if not self._password_hash:
            return False
        try:
            _algorithm, rounds, salt_hex, digest_hex = self._password_hash.split("$", 3)
            candidate = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds)
            ).hex()
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(candidate, digest_hex)

    def login(self, username: str, password: str, *, now: float | None = None) -> SessionRecord:
        current = time.time() if now is None else float(now)
        if not self.configured or self._username is None:
            raise SessionAuthError("尚未完成首次管理者設定。")
        failures = [stamp for stamp in self._failures.get(username, []) if current - stamp < self.lockout_seconds]
        self._failures[username] = failures
        if len(failures) >= self.max_failures:
            raise SessionAuthError("登入失敗次數過多，請稍後再試。")
        if username != self._username or not self._verify_password(password):
            failures.append(current)
            self._failures[username] = failures[-self.max_failures :]
            raise SessionAuthError("帳號或密碼不正確。")
        record = SessionRecord(
            session_id=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(32),
            username=self._username,
            expires_at=current + self.timeout_seconds,
        )
        self._sessions[record.session_id] = record
        return record

    def authenticate(self, session_id: str | None, *, now: float | None = None) -> SessionRecord | None:
        if not session_id:
            return None
        record = self._sessions.get(session_id)
        current = time.time() if now is None else float(now)
        if record is None or record.expires_at <= current:
            self._sessions.pop(session_id, None)
            return None
        return record

    def logout(self, session_id: str | None) -> None:
        if session_id:
            self._sessions.pop(session_id, None)
