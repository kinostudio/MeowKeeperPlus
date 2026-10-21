"""Windows Credential Manager-backed Telegram secret storage.

The Dashboard may submit a credential once over its authenticated local HTTP
session.  Values are never returned to the browser, written to ``config.json``
or included in logs.  Environment variables remain a supported fallback for
portable/diagnostic launches and tests.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CredentialStoreError(RuntimeError):
    """本機 Credential Manager 不可用或作業失敗。"""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TelegramRecipient:
    """一個可由使用者命名、可選擇是否接收通知的 Chat ID。"""

    alias: str
    chat_id: str
    enabled: bool = True
    role: str = "viewer"

    @property
    def recipient_id(self) -> str:
        """Stable opaque identifier; it cannot be used to address Telegram."""

        return hashlib.sha256(self.chat_id.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class TelegramCredentials:
    """Read-only value object; callers must not serialize the values to Web output."""

    bot_token: str = ""
    chat_ids: str = ""
    recipients_json: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.bot_token and (self.chat_ids or self.recipients))

    @property
    def recipients(self) -> tuple[TelegramRecipient, ...]:
        return _decode_recipients(self.recipients_json)

    @property
    def effective_recipients(self) -> tuple[TelegramRecipient, ...]:
        """Return named recipients, or safe local aliases for legacy chat IDs."""

        if self.recipients_json:
            return self.recipients
        return _legacy_recipients(self.chat_ids)

    @property
    def selected_chat_ids(self) -> tuple[str, ...]:
        # A saved empty/disabled list is an explicit choice, not legacy data.
        # Malformed saved data also fails closed rather than enabling old IDs.
        if self.recipients_json:
            return tuple(item.chat_id for item in self.recipients if item.enabled)
        return tuple(item.strip() for item in self.chat_ids.split(",") if item.strip())


def _normalize_chat_ids(chat_ids: Any) -> str:
    raw_chat_ids = str(chat_ids or "").strip()
    parts = [part.strip() for part in re.split(r"[,;\r\n]+", raw_chat_ids) if part.strip()]
    if not parts:
        raise ValueError("至少需要一個 Telegram Chat ID。")
    if any(len(part) > 128 or any(char.isspace() for char in part) for part in parts):
        raise ValueError("Chat ID 格式不正確，請使用數字或 @username，並以逗號分隔。")
    return ",".join(dict.fromkeys(parts))


def _decode_recipients(raw: Any) -> tuple[TelegramRecipient, ...]:
    if not raw:
        return ()
    try:
        payload = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    if not isinstance(payload, list):
        return ()
    result: list[TelegramRecipient] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        alias = str(item.get("alias") or "").strip()
        chat_id = str(item.get("chat_id") or "").strip()
        if alias and chat_id:
            role = str(item.get("role") or "viewer").strip().lower()
            if role not in {"viewer", "operator", "admin"}:
                role = "viewer"
            result.append(TelegramRecipient(
                alias=alias, chat_id=chat_id, enabled=bool(item.get("enabled", True)), role=role,
            ))
    return tuple(result)


def _legacy_recipients(chat_ids: Any) -> tuple[TelegramRecipient, ...]:
    """Expose old comma-separated IDs to the local editor without exposing values."""

    values = tuple(part.strip() for part in str(chat_ids or "").split(",") if part.strip())
    return tuple(
        TelegramRecipient(alias=f"通知對象 {index}", chat_id=chat_id)
        for index, chat_id in enumerate(values, start=1)
    )


def _validate_recipients(recipients: Any) -> tuple[str, str]:
    if not isinstance(recipients, Sequence) or isinstance(recipients, (str, bytes, bytearray)):
        raise TypeError("通知對象必須是陣列。")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(recipients, start=1):
        if not isinstance(item, Mapping):
            raise TypeError("通知對象格式不正確。")
        alias = str(item.get("alias") or f"通知對象 {index}").strip()
        chat_id = str(item.get("chat_id") or "").strip()
        if not alias or len(alias) > 80:
            raise ValueError("通知對象別名不可為空白或超過 80 個字元。")
        if not chat_id:
            raise ValueError(f"通知對象「{alias}」缺少 Chat ID。")
        normalized_chat_id = _normalize_chat_ids(chat_id)
        if "," in normalized_chat_id or normalized_chat_id in seen:
            raise ValueError("每個通知對象只能有一個不重複的 Chat ID。")
        role = str(item.get("role") or "viewer").strip().lower()
        if role not in {"viewer", "operator", "admin"}:
            raise ValueError("Telegram 權限只能是 Viewer、Operator 或 Admin。")
        seen.add(normalized_chat_id)
        normalized.append({
            "alias": alias,
            "chat_id": normalized_chat_id,
            "enabled": bool(item.get("enabled", True)),
            "role": role,
        })
    if not normalized:
        raise ValueError("至少需要一個通知對象。")
    if not any(bool(item["enabled"]) for item in normalized):
        raise ValueError("至少啟用一個通知對象。")
    encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    chat_ids = ",".join(str(item["chat_id"]) for item in normalized if item["enabled"])
    return encoded, chat_ids


def _validate_telegram_credentials(bot_token: Any, chat_ids: Any) -> tuple[str, str]:
    token = str(bot_token or "").strip()
    if not token or len(token) > 256 or any(char.isspace() for char in token):
        raise ValueError("Bot Token 格式不正確，請確認沒有空白或換行。")
    return token, _normalize_chat_ids(chat_ids)


if os.name == "nt":

    class _DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    class _CredentialAttributeW(ctypes.Structure):
        _fields_ = [
            ("Keyword", wintypes.LPWSTR),
            ("Flags", wintypes.DWORD),
            ("ValueSize", wintypes.DWORD),
            ("Value", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    class _CredentialW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.POINTER(_CredentialAttributeW)),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]


class TelegramCredentialStore:
    """Persist Telegram credentials per Windows user using CredWrite/CredRead."""

    _TOKEN_TARGET = "MeowKeeperPlus/telegram/bot_token"
    _CHAT_IDS_TARGET = "MeowKeeperPlus/telegram/chat_ids"
    _RECIPIENTS_TARGET = "MeowKeeperPlus/telegram/recipients"
    _VISION_TARGET_PREFIX = "MeowKeeperPlus/vision/api-key/"
    _CRED_TYPE_GENERIC = 1
    _CRED_PERSIST_LOCAL_MACHINE = 2
    _ERROR_NOT_FOUND = 1168

    def __init__(self, *, api: Any | None = None, fallback_path: str | os.PathLike[str] | None = None) -> None:
        if os.name != "nt":
            raise CredentialStoreError("目前只支援 Windows Credential Manager。")
        default_path = Path(os.getenv("LOCALAPPDATA", Path.home())) / "MeowKeeperPlus" / "secrets" / "telegram.dpapi"
        self._fallback_path = Path(fallback_path or default_path).resolve()
        self._use_dpapi = False
        try:
            self._api = api or ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        except OSError:
            self._api = None
            self._use_dpapi = True
        try:
            self._crypt32 = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
            self._kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
        except OSError as exc:
            raise CredentialStoreError("Windows DPAPI 不可用。") from exc
        self._protect_api = self._crypt32.CryptProtectData
        self._protect_api.argtypes = [
            ctypes.POINTER(_DataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        self._protect_api.restype = wintypes.BOOL
        self._unprotect_api = self._crypt32.CryptUnprotectData
        self._unprotect_api.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        self._unprotect_api.restype = wintypes.BOOL
        self._local_free = self._kernel32.LocalFree
        self._local_free.argtypes = [ctypes.c_void_p]
        self._local_free.restype = ctypes.c_void_p
        if self._api is None:
            return
        self._read_api = self._api.CredReadW
        self._read_api.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(_CredentialW)),
        ]
        self._read_api.restype = wintypes.BOOL
        self._write_api = self._api.CredWriteW
        self._write_api.argtypes = [ctypes.POINTER(_CredentialW), wintypes.DWORD]
        self._write_api.restype = wintypes.BOOL
        self._delete_api = self._api.CredDeleteW
        self._delete_api.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._delete_api.restype = wintypes.BOOL
        self._free_api = self._api.CredFree
        self._free_api.argtypes = [ctypes.c_void_p]
        self._free_api.restype = None

    @property
    def backend_name(self) -> str:
        return "windows_dpapi" if self._use_dpapi else "windows_credential_manager"

    def _error(self) -> CredentialStoreError:
        code = ctypes.get_last_error()
        return CredentialStoreError(f"Windows Credential Manager 作業失敗（{code}）。", code=code)

    def _read(self, target: str) -> str:
        pointer = ctypes.POINTER(_CredentialW)()
        if not self._read_api(target, self._CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            code = ctypes.get_last_error()
            if code == self._ERROR_NOT_FOUND:
                return ""
            raise self._error()
        try:
            size = int(pointer.contents.CredentialBlobSize)
            blob = ctypes.string_at(pointer.contents.CredentialBlob, size)
            return blob.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise CredentialStoreError("Credential Manager 內容無法解碼。") from exc
        finally:
            self._free_api(pointer)

    def _write(self, target: str, value: str) -> None:
        blob = ctypes.create_string_buffer(value.encode("utf-8"))
        credential = _CredentialW()
        credential.Type = self._CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.CredentialBlobSize = len(blob.raw) - 1
        credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = self._CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "MeowKeeper+"
        if not self._write_api(ctypes.byref(credential), 0):
            raise self._error()

    def _delete(self, target: str) -> None:
        if self._delete_api(target, self._CRED_TYPE_GENERIC, 0):
            return
        code = ctypes.get_last_error()
        if code != self._ERROR_NOT_FOUND:
            raise self._error()

    @classmethod
    def _vision_target(cls, profile_reference: str) -> str:
        reference = str(profile_reference or "")
        if len(reference) != 64 or any(char not in "0123456789abcdef" for char in reference):
            raise ValueError("視覺服務金鑰設定識別不正確。")
        return f"{cls._VISION_TARGET_PREFIX}{reference}"

    def read_vision_key(self, profile_reference: str) -> str:
        """Read one provider/model key from Windows Credential Manager.

        Vision keys deliberately do not use the Telegram DPAPI-file fallback:
        if Credential Manager is unavailable, callers must fail closed.
        """
        if self._use_dpapi:
            raise CredentialStoreError("Windows Credential Manager 不可用，視覺服務金鑰未讀取。")
        return self._read(self._vision_target(profile_reference))

    def save_vision_key(self, profile_reference: str, value: str) -> None:
        if self._use_dpapi:
            raise CredentialStoreError("Windows Credential Manager 不可用，視覺服務金鑰未保存。")
        if not isinstance(value, str) or not value or len(value) > 4096 or "\r" in value or "\n" in value:
            raise ValueError("視覺服務金鑰格式不正確。")
        self._write(self._vision_target(profile_reference), value)

    def delete_vision_key(self, profile_reference: str) -> None:
        if self._use_dpapi:
            raise CredentialStoreError("Windows Credential Manager 不可用，視覺服務金鑰未清除。")
        self._delete(self._vision_target(profile_reference))

    def _dpapi(self, payload: bytes, *, protect: bool) -> bytes:
        source = ctypes.create_string_buffer(payload)
        input_blob = _DataBlob(len(payload), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
        output_blob = _DataBlob()
        if protect:
            ok = self._protect_api(
                ctypes.byref(input_blob),
                "MeowKeeper+ Telegram credentials",
                None,
                None,
                None,
                1,
                ctypes.byref(output_blob),
            )
        else:
            ok = self._unprotect_api(
                ctypes.byref(input_blob),
                None,
                None,
                None,
                None,
                1,
                ctypes.byref(output_blob),
            )
        if not ok:
            code = ctypes.get_last_error()
            raise CredentialStoreError(f"Windows DPAPI 作業失敗（{code}）。", code=code)
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            self._local_free(output_blob.pbData)

    def _read_dpapi(self) -> TelegramCredentials:
        if not self._fallback_path.is_file():
            return TelegramCredentials()
        try:
            decrypted = self._dpapi(self._fallback_path.read_bytes(), protect=False)
            payload = json.loads(decrypted.decode("utf-8"))
            if not isinstance(payload, dict):
                raise TypeError("not an object")
            return TelegramCredentials(
                str(payload.get("bot_token") or ""),
                str(payload.get("chat_ids") or ""),
                str(payload.get("recipients") or ""),
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, CredentialStoreError) as exc:
            raise CredentialStoreError("Windows DPAPI 憑證內容無法讀取。") from exc

    def _write_dpapi(self, bot_token: str, chat_ids: str, recipients_json: str = "") -> None:
        payload = json.dumps(
            {"bot_token": bot_token, "chat_ids": chat_ids, "recipients": recipients_json},
            ensure_ascii=False,
        ).encode("utf-8")
        encrypted = self._dpapi(payload, protect=True)
        self._fallback_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._fallback_path.with_suffix(self._fallback_path.suffix + ".tmp")
        try:
            temporary.write_bytes(encrypted)
            temporary.replace(self._fallback_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _delete_dpapi(self) -> None:
        try:
            self._fallback_path.unlink(missing_ok=True)
        except OSError as exc:
            raise CredentialStoreError("Windows DPAPI 憑證無法清除。") from exc

    def read_telegram(self) -> TelegramCredentials:
        if self._use_dpapi:
            return self._read_dpapi()
        try:
            return TelegramCredentials(
                bot_token=self._read(self._TOKEN_TARGET),
                chat_ids=self._read(self._CHAT_IDS_TARGET),
                recipients_json=self._read(self._RECIPIENTS_TARGET),
            )
        except CredentialStoreError as exc:
            if exc.code != 1312:
                raise
            self._use_dpapi = True
            return self._read_dpapi()

    def status(self) -> dict[str, Any]:
        values = self.read_telegram()
        recipients = values.effective_recipients
        return {
            "backend": self.backend_name,
            "bot_token_configured": bool(values.bot_token),
            "chat_ids_configured": bool(values.chat_ids or recipients),
            "recipient_count": len(recipients),
            "recipients": [
                {
                    "alias": item.alias,
                    "recipient_id": item.recipient_id,
                    "chat_id_masked": _mask_chat_id(item.chat_id),
                    "enabled": item.enabled,
                    "role": item.role,
                }
                for item in recipients
            ],
        }

    def save_telegram(self, bot_token: Any, chat_ids: Any, recipients: Any | None = None) -> None:
        token = str(bot_token or "").strip()
        if not token or len(token) > 256 or any(char.isspace() for char in token):
            raise ValueError("Bot Token 格式不正確，請確認沒有空白或換行。")
        previous = self.read_telegram()
        recipients_json = previous.recipients_json
        if recipients is not None:
            recipients_json, normalized_chat_ids = _validate_recipients(recipients)
        else:
            _, normalized_chat_ids = _validate_telegram_credentials(token, chat_ids)
        if self._use_dpapi:
            self._write_dpapi(token, normalized_chat_ids, recipients_json)
            return
        try:
            self._write(self._TOKEN_TARGET, token)
            self._write(self._CHAT_IDS_TARGET, normalized_chat_ids)
            if recipients_json:
                self._write(self._RECIPIENTS_TARGET, recipients_json)
            else:
                self._delete(self._RECIPIENTS_TARGET)
        except (CredentialStoreError, OSError, RuntimeError, ValueError) as exc:
            if isinstance(exc, CredentialStoreError) and exc.code == 1312:
                self._use_dpapi = True
                self._write_dpapi(token, normalized_chat_ids, recipients_json)
                return
            try:
                if previous.bot_token:
                    self._write(self._TOKEN_TARGET, previous.bot_token)
                else:
                    self._delete(self._TOKEN_TARGET)
                if previous.chat_ids:
                    self._write(self._CHAT_IDS_TARGET, previous.chat_ids)
                else:
                    self._delete(self._CHAT_IDS_TARGET)
                if previous.recipients_json:
                    self._write(self._RECIPIENTS_TARGET, previous.recipients_json)
                else:
                    self._delete(self._RECIPIENTS_TARGET)
            except (CredentialStoreError, OSError, RuntimeError, ValueError) as restore_error:
                raise CredentialStoreError("Telegram 憑證保存失敗，且無法復原原有本機憑證。") from restore_error
            if isinstance(exc, CredentialStoreError):
                raise
            raise CredentialStoreError("Telegram 憑證保存失敗，原有本機憑證已保留。") from exc

    def save_recipients(self, recipients: Any) -> None:
        previous = self.read_telegram()
        if not previous.bot_token:
            raise CredentialStoreError("請先保存 Bot Token，再保存通知對象。")
        if not isinstance(recipients, Sequence) or isinstance(recipients, (str, bytes, bytearray)):
            raise TypeError("通知對象必須是陣列。")
        existing = {item.recipient_id: item for item in previous.effective_recipients}
        merged: list[dict[str, Any]] = []
        for item in recipients:
            if not isinstance(item, Mapping):
                raise TypeError("通知對象格式不正確。")
            candidate = dict(item)
            chat_id = str(candidate.get("chat_id") or "").strip()
            if not chat_id:
                recipient_id = str(candidate.get("recipient_id") or "").strip()
                prior = existing.get(recipient_id)
                if prior is None:
                    raise ValueError("既有通知對象缺少可識別的 Chat ID；請重新輸入。")
                candidate["chat_id"] = prior.chat_id
            merged.append(candidate)
        self.save_telegram(previous.bot_token, "", recipients=merged)

    def clear_telegram(self) -> None:
        if self._use_dpapi:
            self._delete_dpapi()
            return
        try:
            self._delete(self._TOKEN_TARGET)
            self._delete(self._CHAT_IDS_TARGET)
            self._delete(self._RECIPIENTS_TARGET)
        except CredentialStoreError as exc:
            if exc.code != 1312:
                raise
            self._use_dpapi = True
            self._delete_dpapi()


def _mask_chat_id(chat_id: str) -> str:
    value = str(chat_id or "")
    if len(value) <= 4:
        return "••••"
    return f"••••{value[-4:]}"


__all__ = ["CredentialStoreError", "TelegramCredentialStore", "TelegramCredentials", "TelegramRecipient"]
