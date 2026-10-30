"""Telegram Bot API 的最小安全推播客戶端。

使用標準函式庫 multipart 請求，避免 V0.1 必須綁定特定 Telegram SDK。
Token 僅存在於 API URL 的記憶體字串中，所有錯誤訊息都不回傳 URL 或 Token。
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class TelegramNotificationError(RuntimeError):
    """Telegram API 呼叫失敗。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        api_error_code: int | None = None,
        reason_code: str = "unknown",
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.api_error_code = api_error_code
        self.reason_code = reason_code
        self.retry_after_seconds = retry_after_seconds

    @property
    def retryable(self) -> bool:
        return self.reason_code in {"rate_limited", "server_error", "network_error"}


def _telegram_error_metadata(
    payload: Mapping[str, Any], *, status_code: int | None = None,
) -> tuple[int | None, str, float | None]:
    """Extract allowlisted Telegram error metadata without retaining raw descriptions."""

    raw_code = payload.get("error_code")
    api_error_code = raw_code if type(raw_code) is int else None
    description = payload.get("description")
    description = description.casefold() if isinstance(description, str) else ""

    parameters = payload.get("parameters")
    retry_after: float | None = None
    if isinstance(parameters, Mapping):
        raw_retry_after = parameters.get("retry_after")
        try:
            parsed_retry_after = float(raw_retry_after)
            if math.isfinite(parsed_retry_after) and parsed_retry_after > 0:
                retry_after = parsed_retry_after
        except (TypeError, ValueError, OverflowError):
            pass

    if "message is not modified" in description:
        reason_code = "message_not_modified"
    elif "message to edit not found" in description or "message not found" in description:
        reason_code = "message_not_found"
    elif "message can't be edited" in description or "message cannot be edited" in description:
        reason_code = "message_cannot_be_edited"
    elif "bot was blocked" in description:
        reason_code = "bot_blocked"
    elif "chat not found" in description:
        reason_code = "chat_not_found"
    elif api_error_code == 429 or status_code == 429:
        reason_code = "rate_limited"
    elif api_error_code == 401 or status_code == 401:
        reason_code = "unauthorized"
    elif api_error_code == 403 or status_code == 403:
        reason_code = "forbidden"
    elif (
        (api_error_code is not None and api_error_code >= 500)
        or (status_code is not None and status_code >= 500)
    ):
        reason_code = "server_error"
    elif (
        (api_error_code is not None and 400 <= api_error_code < 500)
        or (status_code is not None and 400 <= status_code < 500)
    ):
        reason_code = "bad_request"
    else:
        reason_code = "api_rejected"
    return api_error_code, reason_code, retry_after


@dataclass(frozen=True)
class TelegramSendResult:
    chat_id: str
    message_id: int | None
    width: int | None = None
    height: int | None = None
    duration: int | None = None


def _multipart_body(
    fields: Mapping[str, str],
    files: Mapping[str, tuple[str, str, bytes]],
) -> tuple[bytes, str]:
    boundary = f"----MeowKeeper{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    for name, (filename, content_type, content) in files.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                (
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{filename}"\r\n'
                ).encode(),
                f"Content-Type: {content_type}\r\n\r\n".encode("ascii"),
                content,
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _button_style(button: Mapping[str, Any]) -> str | None:
    """Choose a Telegram-supported semantic color without changing button behavior."""

    callback = str(button.get("callback_data") or "")
    text = str(button.get("text") or "").strip()
    parts = callback.split(":")

    if callback.startswith("mkcmd:reset:confirm:") or callback == "mkpref:notifications:off":
        return "danger"
    if callback == "mkpref:notifications:on":
        return "success"

    if len(parts) >= 3 and parts[0] == "cat":
        return {"yes": "success", "no": "danger"}.get(parts[-1])
    if len(parts) >= 3 and parts[0] == "pick":
        return {"both": "success", "no": "danger"}.get(parts[-1], "primary")
    if parts[0] in {"who", "be", "fb"} and len(parts) >= 3:
        action = parts[-1]
        if action == "no":
            return "danger"
        if action in {"other", "unknown", "uncertain", "cancel"}:
            return None
        if parts[0] == "fb" and len(parts) >= 4 and parts[2] == "b":
            return None
        if parts[0] == "be" and len(parts) >= 4 and parts[2] == "c":
            return None
        return "primary"
    if len(parts) >= 3 and parts[0] == "tags":
        return {
            "done": "success",
            "review": "primary",
            "clear": "danger",
        }.get(parts[2])
    if parts[0] in {"trk", "next"}:
        return "primary"

    if text in {"開啟通知", "👁 恢復畫面", "✅ 確認提交", "✅ 是貓咪", "是貓咪"}:
        return "success"
    if text in {"關閉通知", "🔒 隱私模式", "🔄 重啟監控", "❌ 不是貓咪", "不是貓咪", "不是貓"}:
        return "danger"
    if (
        text in {"📷 即時截圖", "🎬 最近影片", "儲存位置", "位置一", "位置二"}
        or text.startswith(("🏠 Dashboard", "📋 回報軌跡"))
        or bool(button.get("url"))
    ):
        return "primary"
    return None


def _styled_reply_markup(reply_markup: Mapping[str, Any]) -> dict[str, Any]:
    """Copy a keyboard and apply accessible built-in Telegram action colors."""

    styled = dict(reply_markup)
    for keyboard_key in ("inline_keyboard", "keyboard"):
        rows = reply_markup.get(keyboard_key)
        if not isinstance(rows, (list, tuple)):
            continue
        styled_rows: list[Any] = []
        for row in rows:
            if not isinstance(row, (list, tuple)):
                styled_rows.append(row)
                continue
            styled_row: list[Any] = []
            for button in row:
                if isinstance(button, Mapping):
                    styled_button = dict(button)
                    if "style" not in styled_button:
                        style = _button_style(styled_button)
                        if style:
                            styled_button["style"] = style
                    styled_row.append(styled_button)
                elif isinstance(button, str):
                    style = _button_style({"text": button})
                    styled_row.append({"text": button, "style": style} if style else button)
                else:
                    styled_row.append(button)
            styled_rows.append(styled_row)
        styled[keyboard_key] = styled_rows
    return styled


class TelegramNotifier:
    """一個 Bot 對多個 Chat ID 發送訊息或事件圖片。"""

    def __init__(self, token: str, *, timeout_seconds: float = 15.0) -> None:
        if not token or any(char.isspace() for char in token):
            raise ValueError("Telegram Bot Token 不可為空白或包含空格。")
        if timeout_seconds <= 0:
            raise ValueError("Telegram API 逾時秒數必須大於零。")
        self._token = token
        self.timeout_seconds = timeout_seconds
        self._rate_lock = threading.Lock()
        self._last_request_at = 0.0

    def _call(
        self,
        method: str,
        *,
        fields: Mapping[str, str] | None = None,
        files: Mapping[str, tuple[str, str, bytes]] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        # Telegram sends and command polling share this client. Keep a small
        # per-client gap so bursts from events and commands do not hammer Bot API.
        with self._rate_lock:
            wait = 0.05 - (time.monotonic() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
            self._last_request_at = time.monotonic()
        if files:
            body, content_type = _multipart_body(fields or {}, files)
        else:
            body = urlencode(fields or {}).encode("utf-8")
            content_type = "application/x-www-form-urlencoded"
        request = Request(
            f"https://api.telegram.org/bot{self._token}/{method}",
            data=body,
            headers={"Content-Type": content_type},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_seconds or self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                error_payload = json.loads(exc.read(4096).decode("utf-8", errors="replace"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                error_payload = {}
            if not isinstance(error_payload, Mapping):
                error_payload = {}
            api_error_code, reason_code, retry_after = _telegram_error_metadata(
                error_payload, status_code=exc.code,
            )
            raise TelegramNotificationError(
                f"Telegram API 請求失敗（HTTP {exc.code}）；請檢查 Token、Chat ID 與權限。",
                status_code=exc.code,
                api_error_code=api_error_code,
                reason_code=reason_code,
                retry_after_seconds=retry_after,
            ) from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            if isinstance(exc, (URLError, TimeoutError)):
                reason_code = "network_error"
            else:
                reason_code = "malformed_response"
            raise TelegramNotificationError(
                "Telegram API 無法連線或回傳格式錯誤。", reason_code=reason_code,
            ) from exc
        if not isinstance(payload, Mapping):
            raise TelegramNotificationError(
                "Telegram API 回傳格式錯誤。", reason_code="malformed_response",
            )
        if not payload.get("ok"):
            api_error_code, reason_code, retry_after = _telegram_error_metadata(payload)
            raise TelegramNotificationError(
                "Telegram API 拒絕此操作；請檢查 Chat ID 與 Bot 權限。",
                api_error_code=api_error_code,
                reason_code=reason_code,
                retry_after_seconds=retry_after,
            )
        return payload

    def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        reply_markup: Mapping[str, Any] | None = None,
    ) -> TelegramSendResult:
        if not chat_id:
            raise ValueError("Chat ID 不可為空白。")
        fields: dict[str, str] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            fields["reply_markup"] = json.dumps(_styled_reply_markup(reply_markup), ensure_ascii=False)
        payload = self._call("sendMessage", fields=fields)
        message_id = payload.get("result", {}).get("message_id")
        return TelegramSendResult(chat_id, int(message_id) if message_id is not None else None)

    def edit_message_reply_markup(
        self, chat_id: str, message_id: int, *, reply_markup: Mapping[str, Any],
    ) -> None:
        if not chat_id or type(message_id) is not int or message_id <= 0:
            raise ValueError("Telegram 按鈕更新的收件者或訊息不正確。")
        self._call("editMessageReplyMarkup", fields={
            "chat_id": chat_id, "message_id": str(message_id),
            "reply_markup": json.dumps(_styled_reply_markup(reply_markup), ensure_ascii=False),
        })

    def edit_message_text(self, chat_id: str, message_id: int, text: str, *, reply_markup: Mapping[str, Any]) -> None:
        if not chat_id or type(message_id) is not int or message_id <= 0 or not 1 <= len(text) <= 4096:
            raise ValueError("Telegram 回報文字更新格式不正確。")
        self._call("editMessageText", fields={"chat_id": chat_id, "message_id": str(message_id),
                                             "text": text, "reply_markup": json.dumps(_styled_reply_markup(reply_markup), ensure_ascii=False)})

    def edit_message_caption(self, chat_id: str, message_id: int, text: str, *, reply_markup: Mapping[str, Any] | None = None) -> None:
        if not chat_id or type(message_id) is not int or message_id <= 0 or not 1 <= len(text.encode("utf-16-le"))//2 <= 1024:
            raise ValueError("Telegram照片回報說明格式不正確。")
        fields: dict[str, Any] = {"chat_id": chat_id, "message_id": str(message_id), "caption": text}
        if reply_markup is not None:
            fields["reply_markup"] = json.dumps(_styled_reply_markup(reply_markup), ensure_ascii=False)
        self._call("editMessageCaption", fields=fields)

    def send_photo(
        self,
        chat_id: str,
        jpeg_bytes: bytes,
        *,
        caption: str,
        reply_markup: Mapping[str, Any] | None = None,
    ) -> TelegramSendResult:
        if not chat_id:
            raise ValueError("Chat ID 不可為空白。")
        if not jpeg_bytes:
            raise ValueError("影格內容不可為空白。")
        payload = self._call(
            "sendPhoto",
            fields={
                "chat_id": chat_id,
                "caption": caption[:1024],
                **({"reply_markup": json.dumps(_styled_reply_markup(reply_markup), ensure_ascii=False)} if reply_markup is not None else {}),
            },
            files={"photo": ("meowkeeper-event.jpg", "image/jpeg", jpeg_bytes)},
        )
        message_id = payload.get("result", {}).get("message_id")
        return TelegramSendResult(chat_id, int(message_id) if message_id is not None else None)

    def send_video(
        self,
        chat_id: str,
        mp4_bytes: bytes,
        *,
        caption: str,
        reply_markup: Mapping[str, Any] | None = None,
        width: int | None = None,
        height: int | None = None,
        duration: float | None = None,
        supports_streaming: bool | None = None,
    ) -> TelegramSendResult:
        """傳送本地已驗證的 MP4；不把路徑或 Token 寫入錯誤訊息。"""

        if not chat_id:
            raise ValueError("Chat ID 不可為空白。")
        if not mp4_bytes:
            raise ValueError("影片內容不可為空白。")
        if (width is None) != (height is None):
            raise ValueError("影片寬度與高度必須同時提供。")
        if width is not None and (type(width) is not int or type(height) is not int or width <= 0 or height <= 0):
            raise ValueError("影片寬度與高度必須為正整數。")
        if duration is not None and (not math.isfinite(float(duration)) or float(duration) <= 0):
            raise ValueError("影片時長必須是有限正數。")
        fields = {
            "chat_id": chat_id,
            "caption": caption[:1024],
            **({"width": str(width), "height": str(height)} if width is not None and height is not None else {}),
            **({"duration": format(float(duration), ".6g")} if duration is not None else {}),
            **({"supports_streaming": "true" if supports_streaming else "false"} if supports_streaming is not None else {}),
            **({"reply_markup": json.dumps(_styled_reply_markup(reply_markup), ensure_ascii=False)} if reply_markup is not None else {}),
        }
        payload = self._call(
            "sendVideo",
            fields=fields,
            files={"video": ("meowkeeper-event.mp4", "video/mp4", mp4_bytes)},
        )
        result = payload.get("result")
        result = result if isinstance(result, dict) else {}
        video = result.get("video")
        video = video if isinstance(video, dict) else {}
        message_id = result.get("message_id")
        def integer_or_none(value: object) -> int | None:
            try:
                return int(value) if value is not None else None
            except (TypeError, ValueError, OverflowError):
                return None

        normalized_message_id = integer_or_none(message_id)
        response_width = integer_or_none(video.get("width"))
        response_height = integer_or_none(video.get("height"))
        response_duration = integer_or_none(video.get("duration"))
        return TelegramSendResult(chat_id, normalized_message_id, response_width, response_height, response_duration)

    def get_webhook_info(self) -> dict[str, Any]:
        """Return webhook metadata; callers must never log the returned URL."""
        return self._call("getWebhookInfo")

    def set_my_commands(self, commands: list[Mapping[str, str]]) -> None:
        normalized: list[dict[str, str]] = []
        for item in commands:
            command = str(item.get("command") or "").strip().lower()
            description = str(item.get("description") or "").strip()
            if not re.fullmatch(r"[a-z0-9_]{1,32}", command) or not description or len(description) > 256:
                raise ValueError("Telegram 指令選單格式不正確。")
            normalized.append({"command": command, "description": description})
        if not normalized or len(normalized) > 100:
            raise ValueError("Telegram 指令數量必須介於 1 到 100。")
        self._call("setMyCommands", fields={"commands": json.dumps(normalized, ensure_ascii=False)})

    def set_commands_menu_button(self) -> None:
        """Show Telegram's native command menu; no Reply Keyboard is retained."""
        self._call("setChatMenuButton", fields={"menu_button": json.dumps({"type": "commands"})})

    def get_updates(self, *, offset: int | None, timeout_seconds: int = 20) -> list[dict[str, Any]]:
        if not 0 <= timeout_seconds <= 30:
            raise ValueError("Telegram 長輪詢逾時必須介於 0 到 30 秒。")
        fields: dict[str, str] = {
            "timeout": str(timeout_seconds),
            "allowed_updates": '["callback_query","message"]',
        }
        if offset is not None:
            fields["offset"] = str(offset)
        payload = self._call("getUpdates", fields=fields, timeout_seconds=timeout_seconds + 5)
        result = payload.get("result")
        if not isinstance(result, list):
            raise TelegramNotificationError("Telegram 回覆格式不正確。")
        return [item for item in result if isinstance(item, dict)]

    def answer_callback_query(
        self, callback_query_id: str, *, text: str, show_alert: bool = False,
    ) -> None:
        if not callback_query_id or len(callback_query_id) > 256:
            raise ValueError("Telegram callback 識別格式不正確。")
        self._call("answerCallbackQuery", fields={
            "callback_query_id": callback_query_id,
            "text": text[:200],
            "show_alert": "true" if show_alert else "false",
        })
