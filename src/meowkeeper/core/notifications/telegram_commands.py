"""授權後的 Telegram 遠端命令；所有耗時操作交由有界通知佇列。"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, ClassVar
from urllib.parse import urlsplit
from uuid import uuid4

from meowkeeper.core.media.media_validation import validate_media
from meowkeeper.core.notifications.cat_preferences import CatNotificationPreferences
from meowkeeper.core.notifications.dispatcher import NotificationDispatcher
from meowkeeper.core.notifications.position_presets import (
    PositionPresetError,
    PositionPresetService,
)
from meowkeeper.core.notifications.privacy import PrivacyService
from meowkeeper.core.notifications.status import RuntimeStatusService
from meowkeeper.core.notifications.telegram import TelegramNotifier, TelegramSendResult
from meowkeeper.storage.database import SQLiteStore


class TelegramCommandRouter:
    """Route private-chat commands to existing Host services, never to new ingests."""

    _COMMAND = re.compile(r"^/([a-z0-9_]{1,32})(?:@[a-z0-9_]{1,64})?(?:\s|$)", re.IGNORECASE)
    _RESET_CALLBACK = re.compile(r"^mkcmd:reset:(confirm|cancel):([0-9a-f-]{36})$")
    _LEVEL: ClassVar[dict[str, int]] = {"viewer": 0, "operator": 1, "admin": 2}
    _BUTTON_COMMANDS: ClassVar[dict[str, str]] = {
        "📷 即時截圖": "snapshot", "🎬 最近影片": "live",
        "📊 系統狀態": "status", "🐱 今日摘要": "today",
        "🔒 隱私模式": "hide", "👁 恢復畫面": "show",
        "🔄 重啟監控": "reset", "🏠 Dashboard": "home",
        "🆔 我的 Chat ID": "start",
        "🔕 靜音 1 小時": "mute", "🔔 開啟貓咪通知": "catnotify",
        "📋 通知狀態": "catnotify",
        "🧪 回饋設定": "feedback",
        "回饋": "feedback", "通知": "notify", "通知設定": "notify", "冷卻": "cd",
        "儲存位置": "savepos", "位置一": "pos1", "位置二": "pos2",
    }
    COMMANDS: ClassVar[tuple[dict[str, str], ...]] = (
        {"command": "start", "description": "取得自己的 Chat ID 與設定說明"},
        {"command": "snapshot", "description": "取得目前最新影格"},
        {"command": "live", "description": "取得最近環形錄影"},
        {"command": "status", "description": "查看即時系統狀態"},
        {"command": "today", "description": "查看今日可用偵測摘要"},
        {"command": "hide", "description": "請求隱私模式（需已設定安全相機位置）"},
        {"command": "show", "description": "請求恢復畫面（需已設定安全相機位置）"},
        {"command": "reset", "description": "確認後安全重新啟動 Host"},
        {"command": "home", "description": "取得 Dashboard 開啟連結"},
        {"command": "catnotify", "description": "管理自己的貓咪事件通知"},
        {"command": "cd", "description": "設定自己的貓咪通知間隔（分鐘）；/cd 15"},
        {"command": "notify", "description": "開關通知或輸入數字靜音小時"},
        {"command": "savepos", "description": "輪流儲存已讀回的位置一／二（需控制權限）"},
        {"command": "pos1", "description": "回到已儲存的位置一（需控制權限）"},
        {"command": "pos2", "description": "回到已儲存的位置二（需控制權限）"},
        {"command": "mute", "description": "將自己的貓咪通知靜音 N 小時"},
        {"command": "sleep", "description": "每日靜音時段（Host 當地時間）；/sleep 23-8"},
        {"command": "unmute", "description": "解除自己的貓咪通知靜音"},
        {"command": "feedback", "description": "選擇是否參與貓咪偵測回饋"},
    )

    def __init__(
        self,
        *,
        notifier: TelegramNotifier,
        dispatcher: NotificationDispatcher,
        controller: Any,
        store: SQLiteStore,
        recipients: Mapping[str, str],
        dashboard_network: Any | None = None,
        go2rtc_status: Callable[[], Any] | None = None,
        tailscale_status: Callable[[], Mapping[str, Any]] | None = None,
        privacy_service: PrivacyService | None = None,
        status_service: RuntimeStatusService | None = None,
        host_restart: Callable[[], Mapping[str, Any]] | None = None,
        cat_notification_preferences: CatNotificationPreferences | None = None,
        live_seconds: float = 15.0,
        ring_retention_seconds: float = 60.0,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.notifier = notifier
        self.dispatcher = dispatcher
        self.controller = controller
        self.store = store
        self.recipients = {
            str(chat_id): role if role in self._LEVEL else "viewer"
            for chat_id, role in recipients.items()
            if str(chat_id).strip()
        }
        self.dashboard_network = dashboard_network
        self.go2rtc_status = go2rtc_status
        self.tailscale_status = tailscale_status
        self.privacy_service = privacy_service or PrivacyService()
        adapter = self.privacy_service.adapter
        pose_path = getattr(adapter, "path", None)
        self.position_presets = (PositionPresetService(adapter, Path(pose_path).with_name("telegram-position-presets.json"))
                                 if isinstance(pose_path, (str, Path)) else None)
        self.host_restart = host_restart
        self.cat_notification_preferences = cat_notification_preferences
        self.live_seconds = max(1.0, min(120.0, float(live_seconds)))
        self.ring_retention_seconds = max(1.0, float(ring_retention_seconds))
        self.logger = logger or (lambda _message: None)
        self.media_dispatcher = NotificationDispatcher(max_pending=2, logger=self.logger)
        self.status_service = status_service or RuntimeStatusService(
            controller=self.controller,
            store=self.store,
            dashboard_network=self.dashboard_network,
            go2rtc_status=self.go2rtc_status,
            tailscale_status=self.tailscale_status,
            telegram_queue_pending=lambda: self.dispatcher.pending_count,
            privacy_adapter_configured=self.privacy_service.adapter is not None,
            logger=self.logger,
        )
        self._restart_after_update: tuple[int, str, str] | None = None
        self._preference_input: dict[str, tuple[str, float]] = {}
        self._preference_input_lock = Lock()

    def close(self, *, wait: bool = True) -> None:
        self.media_dispatcher.shutdown(wait=wait)

    def register_menu(self) -> None:
        self.notifier.set_my_commands(list(self.COMMANDS))
        self.notifier.set_commands_menu_button()

    def handle_message_update(self, message: Mapping[str, Any]) -> bool:
        text = message.get("text")
        if not isinstance(text, str):
            return False
        label_command = self._BUTTON_COMMANDS.get(text.strip())
        match = self._COMMAND.match(text.strip())
        if match is None and label_command is None:
            chat_id, _role = self._authorized_private_message(message)
            if chat_id is not None:
                return self._handle_preference_input(chat_id, text.strip())
            return False
        command = label_command or match.group(1).lower()
        if command == "start":
            # The only public command: disclose only this private sender's ID.
            # It never registers a recipient or grants camera/control access.
            chat = message.get("chat")
            sender = message.get("from")
            if (isinstance(chat, Mapping) and isinstance(sender, Mapping)
                    and chat.get("type") == "private" and sender.get("is_bot") is not True
                    and type(chat.get("id")) is int and chat["id"] > 0
                    and type(sender.get("id")) is int and sender["id"] == chat["id"]):
                own_id = str(chat["id"])
                self._enqueue(own_id, lambda: self._send_text(
                    own_id,
                    "🐱 歡迎使用喵喵守護者+！\n\n"
                    f"你的 Telegram Chat ID：{own_id}\n\n"
                    "請複製此數字，交由管理者在本機 Dashboard → Telegram → 通知對象中新增，"
                    "設定別名、勾選啟用並選擇權限，再套用設定。\n"
                    "加入 Bot 不會自動取得攝影機畫面或控制權限。\n"
                    "請勿在公開群組張貼 Chat ID，也不需要提供密碼或 Bot Token。",
                ))
            return True
        chat_id, role = self._authorized_private_message(message)
        if chat_id is None:
            chat = message.get("chat")
            sender = message.get("from")
            if (isinstance(chat, Mapping) and isinstance(sender, Mapping)
                    and chat.get("type") == "private" and sender.get("is_bot") is not True
                    and type(chat.get("id")) is int and chat["id"] > 0
                    and type(sender.get("id")) is int and sender["id"] == chat["id"]):
                denied_chat = str(chat["id"])
                self._enqueue(denied_chat, lambda: self._send_text(
                    denied_chat, "⚠️ 此 Telegram 帳號沒有權限執行此操作。",
                ))
                self._safe_log(
                    f"Telegram command={command} user_ref={self._chat_ref(denied_chat)} "
                    "result=denied error_code=TELEGRAM_UNAUTHORIZED"
                )
            return True
        required = "operator" if command in {"hide", "show", "reset", "savepos", "pos1", "pos2"} else "viewer"
        if command not in {item["command"] for item in self.COMMANDS}:
            self._enqueue(chat_id, lambda: self._send_text(chat_id, "不支援此指令。請使用喵喵守護者+命令選單。"))
            return True
        if self._LEVEL[role] < self._LEVEL[required]:
            self._enqueue(chat_id, lambda: self._send_text(chat_id, "⚠️ 此 Telegram 帳號沒有權限執行此操作。"))
            return True

        with self._preference_input_lock:
            self._preference_input.pop(chat_id, None)

        handlers = {
            "savepos": lambda: self._send_position(chat_id, None),
            "pos1": lambda: self._send_position(chat_id, 1),
            "pos2": lambda: self._send_position(chat_id, 2),
            "cd": lambda: self._request_preference_input(chat_id, "cooldown") if label_command is not None
            else self._send_text(chat_id, self._cooldown_command(chat_id, text.strip())),
            "notify": lambda: self._request_preference_input(chat_id, "mute"),
            "status": lambda: self._send_text(chat_id, self._status_text()),
            "home": lambda: self._send_home(chat_id),
            "snapshot": lambda: self._send_snapshot(chat_id),
            "live": lambda: self._enqueue_media(chat_id, lambda: self._prepare_live(chat_id)),
            "today": lambda: self._send_text(chat_id, self._today_text()),
            "hide": lambda: self._send_privacy(chat_id, "hide"),
            "show": lambda: self._send_privacy(chat_id, "show"),
            "reset": lambda: self._request_reset(chat_id),
            "catnotify": lambda: self._send_text(
                chat_id, self._cat_notification_command(chat_id, text.strip(), label_command),
            ),
            "mute": lambda: self._send_text(
                chat_id, self._mute_command(chat_id, text.strip(), label_command),
            ),
            "unmute": lambda: self._send_text(chat_id, self._unmute_command(chat_id)),
            "sleep": lambda: self._send_text(chat_id, self._sleep_command(chat_id, text.strip())),
            "feedback": lambda: self._send_text(
                chat_id, self._feedback_command(chat_id, text.strip(), label_command),
            ),
        }
        self._enqueue(chat_id, handlers[command])
        return True

    def handle_callback_update(self, callback: Mapping[str, Any], *, update_id: int) -> bool:
        data = str(callback.get("data") or "")
        if data.startswith("mkpref:"):
            return self._handle_preference_callback(callback)
        if not data.startswith("mkcmd:"):
            return False
        match = self._RESET_CALLBACK.fullmatch(data)
        callback_id = str(callback.get("id") or "")
        message = callback.get("message")
        chat = message.get("chat") if isinstance(message, Mapping) else None
        sender = callback.get("from")
        chat_id = str(chat.get("id")) if isinstance(chat, Mapping) and chat.get("id") is not None else ""
        user_id = str(sender.get("id")) if isinstance(sender, Mapping) and sender.get("id") is not None else ""
        role = self.recipients.get(chat_id)
        if (match is None or chat_id != user_id or role is None
                or not isinstance(chat, Mapping) or chat.get("type") != "private"
                or self._LEVEL[role] < self._LEVEL["operator"]):
            self._answer_callback(callback_id, "此操作無效或沒有權限。", alert=True)
            return True

        action, request_id = match.groups()
        requester_ref = self._chat_ref(chat_id)
        now = datetime.now(timezone.utc).isoformat()
        if action == "cancel":
            cancelled = self.store.cancel_telegram_restart(
                request_id=request_id, requester_ref=requester_ref, cancelled_at=now,
            )
            self._answer_callback(callback_id, "已取消重啟。" if cancelled else "確認已過期，沒有變更。")
            if cancelled:
                self._enqueue(chat_id, lambda: self._send_text(chat_id, "已取消重啟，監控維持原狀。"))
            return True

        confirmed = self.store.confirm_telegram_restart_and_commit_update(
            request_id=request_id, requester_ref=requester_ref, confirmed_at=now,
            update_id=update_id,
        )
        if not confirmed:
            self._answer_callback(callback_id, "確認已過期或已處理，請重新輸入 /reset。", alert=True)
            return True
        self._restart_after_update = (update_id, chat_id, request_id)
        self._answer_callback(callback_id, "已確認，正在準備安全重新啟動。")
        return True

    def after_update_committed(self, update_id: int) -> None:
        pending = self._restart_after_update
        if pending is None or pending[0] != update_id:
            return
        self._restart_after_update = None
        _, chat_id, request_id = pending
        try:
            if self.host_restart is None:
                raise RuntimeError("HOST_RESTART_UNAVAILABLE")
            result = self.host_restart()
            if str(result.get("status")) != "restarting":
                raise RuntimeError("HOST_RESTART_REJECTED")
        except Exception as exc:  # noqa: BLE001 - durable failure, with best-effort user notice
            self.store.finish_telegram_restart(
                request_id=request_id, status="failed", updated_at=datetime.now(timezone.utc).isoformat(),
            )
            self._safe_log(f"⚠️ Host 安全重啟未啟動：{type(exc).__name__}。")
            self._enqueue(
                chat_id,
                lambda: self._send_text(chat_id, "⚠️ Host 重啟未啟動；監控維持目前狀態，請查看 Dashboard 診斷。"),
            )

    def report_pending_restart_results(self) -> int:
        """Report a confirmed reset only after the new Host passed /healthz."""
        sent = 0
        selected = {self._chat_ref(chat_id): chat_id for chat_id in self.recipients}
        for row in self.store.pending_telegram_restart_requests(limit=8):
            chat_id = selected.get(str(row["requester_ref"]))
            if chat_id is None:
                self._safe_log("⚠️ Host 重啟完成，但原 Telegram 操作者不在目前授權名單；結果保留待查。")
                continue
            request_id = str(row["request_id"])

            def deliver(selected_chat: str = chat_id, selected_id: str = request_id) -> None:
                self._send_text(selected_chat, self._restart_result_text())
                self.store.finish_telegram_restart(
                    request_id=selected_id, status="completed",
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )

            if self._enqueue(chat_id, deliver):
                sent += 1
        return sent

    def _request_reset(self, chat_id: str) -> None:
        request_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        created = self.store.create_telegram_restart_confirmation(
            request_id=request_id, requester_ref=self._chat_ref(chat_id), created_at=now,
        )
        if not created:
            self._send_text(chat_id, "已有一筆重啟確認正在等待或執行；請先完成該項操作。")
            return
        markup = {"inline_keyboard": [[
            {"text": "✅ 確認重新啟動", "callback_data": f"mkcmd:reset:confirm:{request_id}"},
            {"text": "取消", "callback_data": f"mkcmd:reset:cancel:{request_id}"},
        ]]}
        self._send_text(
            chat_id,
            "⚠️ 是否要安全重新啟動 MeowKeeper+ Host？監控會短暫中斷，完成後會檢查 Dashboard 健康狀態。",
            reply_markup=markup,
        )

    def _send_privacy(self, chat_id: str, action: str) -> None:
        result = self.privacy_service.hide() if action == "hide" else self.privacy_service.show()
        prefix = "🔒" if action == "hide" else "👁"
        message = str(result.get("message_zh_tw") or "攝影機隱私控制目前不可用。")
        self._send_text(chat_id, f"{prefix} {message}")

    def _send_snapshot(self, chat_id: str) -> None:
        status = self.controller.get_status()
        metrics = status.get("metrics", {}) if isinstance(status, Mapping) else {}
        source = metrics.get("source", {}) if isinstance(metrics, Mapping) else {}
        source = source if isinstance(source, Mapping) else {}
        frame_at = source.get("last_frame_at")
        if source.get("status") not in {"running", "streaming", "connected"} or not self._is_recent(frame_at, 20.0):
            self._safe_log("Telegram command=snapshot result=unavailable error_code=FRAME_UNAVAILABLE")
            self._send_text(chat_id, "📷 來源未連線或最新影格已過期，目前無法提供即時畫面；請查看系統狀態。")
            return
        path = self.controller.create_snapshot()
        if not isinstance(path, Path) or not path.is_file():
            self._send_text(chat_id, "📷 目前沒有可用的最新影格；未重新連線或建立新的攝影機工作。")
            return
        try:
            payload = path.read_bytes()
        except OSError:
            self._send_text(chat_id, "📷 最新影格暫時無法讀取；監控來源沒有因此重啟。")
            return
        if len(payload) > 15 * 1024 * 1024 or not payload.startswith(b"\xff\xd8\xff"):
            self._send_text(chat_id, "📷 最新影格格式無效或檔案過大，沒有傳送。")
            return
        resolution = source.get("resolution")
        timestamp = self._display_time(frame_at)
        caption = f"📷 喵喵守護者+ 最新影格\n時間：{timestamp}"
        if resolution:
            caption += f"\n畫面：{str(resolution)[:32]}"
        source_type = self.status_service.source_type
        caption += f"\n來源：{source_type if source_type in {'rtsp', 'youtube', 'video', 'xiaomi', 'mock', 'external'} else '未確認'}"
        self._retry_send(lambda: self.notifier.send_photo(chat_id, payload, caption=caption), kind="photo")

    def _prepare_live(self, chat_id: str) -> None:
        requested = min(self.live_seconds, self.ring_retention_seconds)
        result = self.controller.get_recent_clip(requested, 0.0)
        path = getattr(result, "path", None)
        if getattr(result, "status", None) != "completed" or not isinstance(path, Path) or not path.is_file():
            self._send_text(chat_id, "🎬 目前沒有可輸出的環形錄影片段；沒有啟動新的攝影機連線。")
            return
        temporary: Path | None = None
        try:
            validation = validate_media(path)
            if not validation.valid:
                self._send_text(chat_id, "🎬 最近片段無法通過影片檢查，未傳送；監控來源保持運作。")
                return
            candidate = path
            if path.stat().st_size > 45 * 1024 * 1024 or not validation.telegram_compatible:
                temporary = path.with_name(f"telegram_live_{uuid4().hex}.mp4")
                transcode = getattr(self.controller, "transcode_clip_for_telegram", None)
                if not callable(transcode):
                    self._send_text(chat_id, "🎬 目前 Host 沒有可用的 H.264 相容轉換器；影片未傳送。")
                    return
                converted = transcode(path, temporary)
                candidate = getattr(converted, "path", None)
                validation = getattr(converted, "validation", None)
                if (
                    getattr(converted, "status", None) != "completed"
                    or not isinstance(candidate, Path)
                    or not candidate.is_file()
                    or validation is None
                    or not validation.telegram_compatible
                ):
                    self._send_text(chat_id, "🎬 即時影片未能轉成可播放的 H.264 MP4；監控仍在運作，沒有傳送不相容影片。")
                    return
            if candidate.stat().st_size > 45 * 1024 * 1024:
                self._send_text(chat_id, "🎬 H.264 影片仍超過 Telegram 傳送大小，未傳送；監控仍在運作。")
                return
            payload = candidate.read_bytes()
        except OSError:
            self._send_text(chat_id, "🎬 最近片段目前無法讀取；影片仍保留在本機。")
            return
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    self._safe_log("⚠️ Telegram 即時轉碼暫存檔清理失敗；下次啟動時依暫存保存政策清理。")
        if len(payload) < 12 or payload[4:8] != b"ftyp":
            self._send_text(chat_id, "🎬 即時片段不是可驗證的 MP4，沒有傳送。")
            return
        duration = validation.duration_seconds or getattr(result, "actual_duration_seconds", None)
        shown_duration = f"{float(duration):.1f} 秒" if isinstance(duration, (int, float)) else "已驗證"
        resolution = f"{validation.width}×{validation.height}" if validation.width and validation.height else "已驗證"
        caption = (
            "🎥 喵喵守護者+ 即時攝影機畫面\n"
            f"編碼：H.264（已驗證）｜畫面：{resolution}\n"
            f"片段長度：{shown_duration}\n來源：既有共享環形快取"
        )
        self._enqueue(
            chat_id,
            lambda: self._retry_send(
                lambda: self.notifier.send_video(
                    chat_id,
                    payload,
                    caption=caption,
                    width=validation.width,
                    height=validation.height,
                    duration=duration,
                    supports_streaming=True,
                ),
                kind="video",
            ),
        )

    def _send_home(self, chat_id: str) -> None:
        buttons: list[list[dict[str, str]]] = []
        urls: list[str] = []
        if self.dashboard_network is not None:
            snapshot = self.dashboard_network.snapshot()
            if snapshot.get("lan_access_enabled"):
                for raw in snapshot.get("urls", []):
                    safe = self._safe_lan_url(raw)
                    if safe and safe not in urls:
                        urls.append(safe)
        if callable(self.tailscale_status):
            try:
                status = self.tailscale_status()
                remote = status.get("remote_dashboard") if isinstance(status, Mapping) else None
                safe = self._safe_remote_url(remote.get("url")) if isinstance(remote, Mapping) else None
                if safe and safe not in urls:
                    urls.append(safe)
            except Exception as exc:  # noqa: BLE001 - unavailable remote URL is not fatal
                self._safe_log(f"⚠️ Telegram Dashboard 連結狀態暫不可用：{type(exc).__name__}。")
        for index, url in enumerate(urls, 1):
            buttons.append([{"text": f"🏠 開啟 Dashboard {index}", "url": url}])
        if buttons:
            self._send_text(chat_id, "🏠 可用的 MeowKeeper+ Dashboard 連結：", reply_markup={"inline_keyboard": buttons})
        else:
            self._send_text(chat_id, "🏠 目前沒有可安全分享的 LAN／Tailscale Dashboard 網址；未提供 localhost 或 wildcard 連結。")

    def _status_text(self) -> str:
        return self.status_service.command_text()

    def _today_text(self) -> str:
        now_local = datetime.now().astimezone()
        start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = start_local + timedelta(days=1)
        summary = self.store.daily_detector_alert_summary(
            start_at=start_local.astimezone(timezone.utc).isoformat(),
            end_at=end_local.astimezone(timezone.utc).isoformat(),
        )
        coverage = self.store.daily_monitoring_summary(day=now_local.date().isoformat())
        monitored = coverage["monitoring_seconds"]
        visible = coverage["cat_active_seconds"]
        try:
            status = self.controller.get_status()
            started = datetime.fromisoformat(str(status.get("started_at"))) if status.get("started_at") else None
            state = str(status.get("status") or "")
            active_start = max(start_local, started.astimezone()) if started and state in {"running", "starting"} else None
            current_session = max(0, int((now_local - active_start).total_seconds())) if active_start else None
        except (TypeError, ValueError, OverflowError):
            current_session = None
        session_text = self._duration(current_session) if current_session is not None else "不可用（未持久保存整日監控時長）"
        return "\n".join((
            f"🐱 今日摘要（{now_local:%Y-%m-%d}）",
            f"本次 Host 監控時長：{session_text}",
            "已保存有效影格取樣時間：" + (self._duration(int(monitored)) if monitored is not None else "尚無已保存資料"),
            "貓咪偵測出現取樣時間：" + (self._duration(int(visible)) if visible is not None else "尚未分析／尚無資料"),
            f"真實 detector 貓咪告警：{summary['detector_count']} 次",
            f"模擬告警（不納入真實偵測）：{summary['simulated_count']} 次",
            "睡眠／進食／喝水／玩耍時長：尚未完成家庭實拍驗收，不提供推估數字。",
            "取樣時間只累計相鄰有效影格；斷線、未知與未保存區間不補算，也不等同玩耍活動。",
        ))

    def _restart_result_text(self) -> str:
        try:
            status = self.controller.get_status()
            state = str(status.get("status") or "unknown")
            metrics = status.get("metrics", {})
            source = metrics.get("source", {}) if isinstance(metrics, Mapping) else {}
            connected = isinstance(source, Mapping) and self._is_recent(source.get("last_frame_at"), 20.0)
        except Exception:  # noqa: BLE001 - health result must fail closed
            state, connected = "unknown", False
        return "\n".join((
            "🔄 MeowKeeper+ Host 已重新啟動，Dashboard /healthz 已通過。",
            f"Host：{'🟢' if state in {'running','starting','waiting_for_source'} else '🔴'} {state}",
            f"Camera：{'🟢 近期影格可用' if connected else '⚪ 尚無近期影格，請查看 Dashboard'}",
        ))

    def _send_text(self, chat_id: str, text: str, *, reply_markup: Mapping[str, Any] | None = None) -> None:
        if reply_markup is None:
            reply_markup = self._reply_keyboard(chat_id)
        self._retry_send(lambda: self.notifier.send_message(chat_id, text, reply_markup=reply_markup))

    def _send_position(self, chat_id: str, slot: int | None) -> None:
        if self.position_presets is None:
            self._send_text(chat_id, "目前沒有可讀回座標的 Xiaomi PTZ 控制；未移動或儲存位置。")
            return
        def operate() -> None:
            try:
                result = self.position_presets.save_next() if slot is None else self.position_presets.go_to(slot)
                self._send_text(chat_id, str(result["message_zh_tw"]))
            except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as exc:
                reason = str(exc) if isinstance(exc, PositionPresetError) else f"控制或設定讀取失敗（{type(exc).__name__}）"
                self.logger(f"Telegram 位置操作未完成：{reason}；訊息送達不代表定位成功。")
                self._send_text(chat_id, f"📍 位置操作未完成\n\n原因：{reason}\n\n請確認目前攝影機及位置讀回；沒有宣稱定位成功。")
        self._enqueue_media(chat_id, operate)

    def _request_preference_input(self, chat_id: str, mode: str) -> None:
        if self.cat_notification_preferences is None:
            self._send_text(chat_id, "通知偏好功能尚未啟用。")
            return
        with self._preference_input_lock:
            self._preference_input[chat_id] = (mode, time.monotonic() + 300)
        if mode == "mute":
            self._send_text(chat_id,
                self._cat_notification_command(chat_id, "/catnotify status", None) + "\n\n"
                "通知設定：點選開啟／關閉；或直接輸入整數小時暫時靜音，例如 2 代表靜音 2 小時。\n"
                "數字輸入五分鐘內有效；輸入「取消」可返回。不影響偵測及本機錄影。",
                reply_markup={"inline_keyboard": [[
                    {"text": "開啟通知", "callback_data": "mkpref:notifications:on"},
                    {"text": "關閉通知", "callback_data": "mkpref:notifications:off"},
                ]]})
        else:
            self._send_text(chat_id,
                self._cooldown_command(chat_id, "/cd") + "\n\n請直接輸入冷卻分鐘數，例如 15。"
                "0 取消個人冷卻；範圍 0～10080。五分鐘內有效，輸入「取消」可返回。")

    def _handle_preference_input(self, chat_id: str, text: str) -> bool:
        with self._preference_input_lock:
            pending = self._preference_input.get(chat_id)
            if pending is None:
                return False
            if time.monotonic() >= pending[1]:
                self._preference_input.pop(chat_id, None)
                self._enqueue(chat_id, lambda: self._send_text(chat_id, "設定輸入已過期，請重新點選通知或冷卻。"))
                return True
            if text == "取消":
                self._preference_input.pop(chat_id, None)
                self._enqueue(chat_id, lambda: self._send_text(chat_id, "已取消，設定未更改。"))
                return True
            if not re.fullmatch(r"[0-9]{1,5}", text):
                self._enqueue(chat_id, lambda: self._send_text(chat_id, "請輸入整數數字，或輸入「取消」。"))
                return True
            value = int(text)
            mode = pending[0]
            if (mode == "mute" and not 1 <= value <= 168) or (mode == "cooldown" and not 0 <= value <= 10080):
                self._enqueue(chat_id, lambda: self._send_text(
                    chat_id, "靜音須為 1～168 小時；冷卻須為 0～10080 分鐘。請重新輸入。"))
                return True
            self._preference_input.pop(chat_id, None)
        return self._enqueue(chat_id, lambda: self._send_text(chat_id,
            self._mute_command(chat_id, f"/mute {value}", None) if mode == "mute"
            else self._cooldown_command(chat_id, f"/cd {value}")))

    def _handle_preference_callback(self, callback: Mapping[str, Any]) -> bool:
        message = callback.get("message")
        chat = message.get("chat") if isinstance(message, Mapping) else None
        sender = callback.get("from")
        chat_id = str(chat.get("id")) if isinstance(chat, Mapping) else ""
        user_id = str(sender.get("id")) if isinstance(sender, Mapping) else ""
        action = str(callback.get("data") or "").removeprefix("mkpref:notifications:")
        callback_id = str(callback.get("id") or "")
        if (chat_id not in self.recipients or user_id != chat_id or
                not isinstance(chat, Mapping) or chat.get("type") != "private" or
                not isinstance(sender, Mapping) or sender.get("is_bot") is True or action not in {"on", "off"}):
            self._answer_callback(callback_id, "沒有權限或選項已失效。", alert=True)
            return True
        with self._preference_input_lock:
            self._preference_input.pop(chat_id, None)
        def apply_choice() -> None:
            preferences = self.cat_notification_preferences
            if preferences is not None and action == "on":
                try:
                    preferences.unmute(chat_id)
                except OSError:
                    self._send_text(chat_id, "通知設定無法保存；沒有解除靜音。")
                    return
            self._send_text(chat_id, self._cat_notification_command(chat_id, f"/catnotify {action}", None))
        queued = self._enqueue(chat_id, apply_choice)
        self._answer_callback(callback_id, "正在套用通知設定。" if queued else "設定佇列忙碌，請稍後重試。")
        return True

    def _cooldown_command(self, chat_id: str, text: str) -> str:
        preferences = self.cat_notification_preferences
        if preferences is None:
            return "⚠️ 通知偏好功能尚未啟用；請查看 Host 診斷。"
        parts = text.split()
        if len(parts) > 2 or (len(parts) == 2 and not re.fullmatch(r"[0-9]{1,5}", parts[1])):
            return "用法：/cd 查看間隔；/cd 15 每 15 分鐘最多一次；/cd 0 取消個人間隔。範圍：0～10080 分鐘。"
        try:
            if len(parts) == 2:
                preferences.set_cooldown_minutes(chat_id, int(parts[1]))
            state = preferences.status(chat_id)
        except ValueError:
            return "通知間隔須為 0 至 10080 分鐘的整數，例如 /cd 15。"
        except OSError:
            return "⚠️ 無法安全保存通知間隔；未套用新設定。"
        if state["storage_error"]:
            return "⚠️ 通知設定檔無法讀取；為避免誤發，貓咪通知已暫停。"
        minutes = state["cooldown_minutes"]
        interval = f"每 {minutes} 分鐘最多一次" if minutes else "未設定額外個人間隔（仍遵守系統事件冷卻）"
        remaining = state["cooldown_remaining_seconds"]
        return (f"🐱 你的貓咪自動通知：{interval}。\n"
                f"目前冷卻剩餘：{remaining} 秒。\n"
                "只影響你的一般貓咪告警與逐軌跡摘要；其他使用者、偵測與本機錄影不受影響。\n"
                "立即生效並保留至下次啟動；不會解除通知關閉或靜音。")

    def _cat_notification_command(self, chat_id: str, text: str, label_command: str | None) -> str:
        preferences = self.cat_notification_preferences
        if preferences is None:
            return "⚠️ 通知偏好功能尚未啟用；請查看 Host 診斷。"
        action = text.split(maxsplit=1)[1].strip().lower() if label_command is None and " " in text else "status"
        if text == "🔔 開啟貓咪通知":
            action = "on"
        if action not in {"on", "off", "status"}:
            return "用法：/catnotify on、/catnotify off 或 /catnotify status。"
        try:
            if action != "status":
                preferences.set_enabled(chat_id, action == "on")
            state = preferences.status(chat_id)
        except OSError:
            return "⚠️ 無法安全保存通知設定；為避免誤發，貓咪通知維持暫停。"
        if state["storage_error"]:
            return "⚠️ 通知設定檔無法讀取；為避免誤發，貓咪通知已暫停。"
        if not state["enabled"]:
            return "🔕 你的貓咪事件通知已關閉；偵測及本機錄影仍持續。輸入 /catnotify on 可重新開啟。"
        if state.get("sleep_active"):
            return self._sleep_command(chat_id, "/sleep")
        if state["muted"]:
            until = state["mute_until"]
            assert isinstance(until, datetime)
            return f"🔕 你的貓咪通知靜音至 {until.astimezone():%Y-%m-%d %H:%M}；/unmute 可提前解除。"
        return "🔔 你的貓咪事件通知已開啟。/cd 15 設定每 15 分鐘最多一次；/mute 1 靜音 1 小時；/sleep 查看每日靜音。"

    def _sleep_command(self, chat_id: str, text: str) -> str:
        preferences = self.cat_notification_preferences
        if preferences is None:
            return "⚠️ 通知偏好功能尚未啟用；請查看 Host 診斷。"
        argument = text.split(maxsplit=1)[1].strip().lower() if " " in text else "status"
        try:
            if argument == "off":
                preferences.clear_sleep_window(chat_id)
                return "已關閉每日靜音；通知仍遵守通知開關、臨時靜音與冷卻設定。"
            if argument != "status":
                match = re.fullmatch(r"([0-9]{1,2})-([0-9]{1,2})", argument)
                if match is None:
                    raise ValueError("invalid sleep hours")
                preferences.set_sleep_window(chat_id, int(match[1]), int(match[2]))
            state = preferences.status(chat_id)
            if state["storage_error"]:
                return "⚠️ 通知設定檔無法讀取；貓咪通知已暫停。"
        except ValueError:
            return "用法：/sleep 23-8（每日 23:00 至翌日 08:00）；整點為 0～23，起訖不可相同。/sleep off 關閉。"
        except OSError:
            return "⚠️ 無法安全保存每日靜音設定；請查看 Host 診斷。"
        window = state.get("sleep_window")
        if not isinstance(window, dict):
            return "尚未設定每日靜音。輸入 /sleep 23-8 設定，/sleep off 關閉；依 Host 電腦當地時間。"
        active = "目前在靜音時段" if state["sleep_active"] else "目前不在靜音時段"
        return (f"每日 {window['start_hour']:02d}:00 至 {window['end_hour']:02d}:00 不傳送貓咪事件通知"
                f"（依 Host 電腦當地時間；{active}）。偵測及本機錄影仍持續，手動指令仍可使用。"
                "/sleep off 關閉每日靜音。")

    def _feedback_command(self, chat_id: str, text: str, label_command: str | None) -> str:
        preferences = self.cat_notification_preferences
        if preferences is None:
            return "⚠️ 回饋偏好功能尚未啟用；請查看 Host 診斷。"
        action = text.split(maxsplit=1)[1].strip().lower() if label_command is None and " " in text else "status"
        if text == "回饋":
            action = "off" if preferences.feedback_enabled(chat_id) else "on"
        if action not in {"on", "off", "status"}:
            return "用法：/feedback on、/feedback off 或 /feedback status。"
        try:
            if action != "status":
                preferences.set_feedback_enabled(chat_id, action == "on")
            enabled = preferences.feedback_enabled(chat_id)
            storage_error = preferences.status(chat_id)["storage_error"]
        except OSError:
            return "⚠️ 無法安全保存回饋設定；回饋維持關閉。"
        if storage_error:
            return "⚠️ 回饋設定檔無法讀取；回饋維持關閉。"
        if text == "回饋":
            return ("✅ 已開啟你的回饋參與；未來告警可回報貓咪與行為。" if enabled else "已關閉你的回饋參與；貓咪通知仍遵守原設定。") + "再次點「回饋」即可切換。"
        if enabled:
            return "✅ 你已選擇參與貓咪偵測回饋；未來的貓咪告警會附上回饋按鈕。輸入 /feedback off 可退出。"
        return "你目前未參與回饋；貓咪告警仍會照常傳送。輸入 /feedback on 可參與；/feedback off 可退出。"

    def _mute_command(self, chat_id: str, text: str, label_command: str | None) -> str:
        preferences = self.cat_notification_preferences
        if preferences is None:
            return "⚠️ 通知偏好功能尚未啟用；請查看 Host 診斷。"
        raw = "1" if label_command is not None else (text.split(maxsplit=1)[1].strip() if " " in text else "")
        if not raw.isdecimal() or not 1 <= int(raw) <= 168:
            return "用法：/mute N，N 為 1～168 小時的整數；例如 /mute 3。"
        try:
            until = preferences.mute_hours(chat_id, int(raw))
        except OSError:
            return "⚠️ 無法安全保存靜音設定；為避免誤發，貓咪通知維持暫停。"
        return f"🔕 你的貓咪事件通知已靜音 {raw} 小時，至 {until.astimezone():%Y-%m-%d %H:%M}。偵測及本機錄影仍持續。"

    def _unmute_command(self, chat_id: str) -> str:
        preferences = self.cat_notification_preferences
        if preferences is None:
            return "⚠️ 通知偏好功能尚未啟用；請查看 Host 診斷。"
        try:
            preferences.unmute(chat_id)
        except OSError:
            return "⚠️ 無法安全保存通知設定；為避免誤發，貓咪通知維持暫停。"
        if preferences.status(chat_id)["enabled"]:
            return "🔔 已解除臨時靜音；通知仍遵守每日 /sleep 時段與冷卻設定。"
        return "已解除暫時靜音，但通知總開關仍關閉；輸入 /catnotify on 才會收到貓咪通知。"

    def _reply_keyboard(self, chat_id: str) -> dict[str, Any]:
        role = self.recipients.get(chat_id)
        rows = [["🆔 我的 Chat ID"]]
        if role in self._LEVEL:
            rows = [
                ["📷 即時截圖", "🎬 最近影片"],
                ["📊 系統狀態", "🐱 今日摘要"],
                ["回饋", "通知", "冷卻"],
                ["🏠 Dashboard", "🆔 我的 Chat ID"],
            ]
            if self._LEVEL[role] >= self._LEVEL["operator"]:
                rows.insert(2, ["🔒 隱私模式", "👁 恢復畫面"])
                rows.append(["儲存位置", "位置一", "位置二"])
                rows.append(["🔄 重啟監控"])
        return {
            "keyboard": [[{"text": label} for label in row] for row in rows],
            "is_persistent": True,
            "resize_keyboard": True,
            "one_time_keyboard": False,
            "input_field_placeholder": "點選喵喵守護者+ 操作",
        }

    def _retry_send(self, operation: Callable[[], Any], *, kind: str = "message") -> Any:
        started = time.monotonic()
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                result = operation()
            except Exception as exc:  # noqa: BLE001 - bounded retries stay off ingest and poller
                last_error = exc
                if attempt < 2:
                    time.sleep(0.4 * (2**attempt))
                continue
            # Only the notifier's typed receipt establishes API acceptance.
            # Never serialize the raw response, message content or Chat ID.
            if isinstance(result, TelegramSendResult):
                message_id = result.message_id
                if isinstance(message_id, int) and not isinstance(message_id, bool) and message_id > 0:
                    self._safe_log(
                        f"Telegram 命令回條 time={datetime.now(timezone.utc).isoformat()} "
                        f"kind={kind} user_ref={self._chat_ref(result.chat_id)} "
                        f"api_ok=true message_id={message_id} attempts={attempt + 1} "
                        f"duration_ms={int((time.monotonic() - started) * 1000)}"
                    )
                else:
                    self._safe_log(f"⚠️ Telegram 命令缺少有效 message_id：kind={kind} receipt=unverified。")
            else:
                self._safe_log(f"⚠️ Telegram 命令沒有可驗證回條：kind={kind} receipt=unverified。")
            return result
        assert last_error is not None
        self._safe_log(
            f"⚠️ Telegram 命令傳送失敗：kind={kind} attempts=3 "
            f"error_code=TELEGRAM_SEND_FAILED error_type={type(last_error).__name__}。"
        )
        raise last_error

    def _enqueue(self, chat_id: str, operation: Callable[[], Any]) -> bool:
        try:
            future = self.dispatcher.submit(operation)
        except RuntimeError as exc:
            self._safe_log(f"⚠️ Telegram 命令佇列未執行：{type(exc).__name__}。")
            return False

        def finished(result: Any) -> None:
            try:
                result.result()
            except Exception as exc:  # noqa: BLE001 - no command text or IDs in logs
                self._safe_log(f"⚠️ Telegram 命令回覆未送達：{type(exc).__name__}。")

        future.add_done_callback(finished)
        return True

    def _enqueue_media(self, chat_id: str, operation: Callable[[], Any]) -> bool:
        try:
            future = self.media_dispatcher.submit(operation)
        except RuntimeError as exc:
            self._safe_log(f"⚠️ Telegram 媒體工作未執行：{type(exc).__name__}。")
            return False

        def finished(result: Any) -> None:
            try:
                result.result()
            except Exception as exc:  # noqa: BLE001 - media export failure is isolated from the poller
                self._safe_log(f"⚠️ Telegram 媒體命令失敗：{type(exc).__name__}。")

        future.add_done_callback(finished)
        return True

    def _authorized_private_message(self, message: Mapping[str, Any]) -> tuple[str | None, str]:
        chat = message.get("chat")
        sender = message.get("from")
        if not isinstance(chat, Mapping) or chat.get("type") != "private" or not isinstance(sender, Mapping):
            return None, "viewer"
        chat_id = str(chat.get("id") or "")
        user_id = str(sender.get("id") or "")
        if not chat_id or chat_id != user_id:
            return None, "viewer"
        role = self.recipients.get(chat_id)
        return (chat_id, role) if role else (None, "viewer")

    def _answer_callback(self, callback_id: str, text: str, *, alert: bool = False) -> None:
        if not callback_id:
            return
        try:
            self.notifier.answer_callback_query(callback_id, text=text, show_alert=alert)
        except Exception as exc:  # noqa: BLE001 - callback processing must continue
            self._safe_log(f"⚠️ Telegram 命令確認提示未送達：{type(exc).__name__}。")

    @staticmethod
    def _safe_lan_url(raw: Any) -> str | None:
        try:
            parts = urlsplit(str(raw))
            address = ipaddress.ip_address(parts.hostname or "")
            if (parts.scheme != "http" or not address.is_private or address.is_loopback
                    or address.is_link_local or address.is_unspecified or address.is_reserved):
                return None
            if parts.username or parts.password or parts.port is None:
                return None
            return str(raw)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_remote_url(raw: Any) -> str | None:
        try:
            parts = urlsplit(str(raw))
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                return None
            if (parts.username or parts.password or parts.query or parts.fragment
                    or parts.hostname.lower() in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}):
                return None
            return str(raw)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _chat_ref(chat_id: str) -> str:
        return hashlib.sha256(chat_id.encode("utf-8")).hexdigest()

    @staticmethod
    def _display_time(value: Any) -> str:
        try:
            parsed = datetime.fromisoformat(str(value))
            return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _is_recent(value: Any, max_age: float) -> bool:
        try:
            parsed = datetime.fromisoformat(str(value))
            if parsed.tzinfo is None:
                return False
            return 0 <= (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() <= max_age
        except (TypeError, ValueError, OverflowError):
            return False

    @staticmethod
    def _duration(seconds: int) -> str:
        hours, remainder = divmod(seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        return f"{hours} 小時 {minutes} 分鐘"

    def _safe_log(self, message: str) -> None:
        try:
            self.logger(message)
        except Exception:  # noqa: BLE001 - logs are never allowed to break commands
            self.logger = lambda _message: None


__all__ = ["TelegramCommandRouter"]
