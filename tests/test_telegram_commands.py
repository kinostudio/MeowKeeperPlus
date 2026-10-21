from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from meowkeeper.core.media.clip_assembler import ClipAssemblyResult
from meowkeeper.core.media.media_validation import MediaValidationResult
from meowkeeper.core.notifications.cat_preferences import CatNotificationPreferences
from meowkeeper.core.notifications.dispatcher import NotificationDispatcher
from meowkeeper.core.notifications.position_presets import PositionPresetService
from meowkeeper.core.notifications.privacy import PrivacyService
from meowkeeper.core.notifications.telegram import TelegramSendResult
from meowkeeper.core.notifications.telegram_commands import TelegramCommandRouter
from meowkeeper.core.notifications.telegram_feedback import TelegramFeedbackPoller
from meowkeeper.storage.database import SQLiteStore


class _Notifier:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.photos: list[dict[str, object]] = []
        self.videos: list[dict[str, object]] = []
        self.answers: list[dict[str, object]] = []
        self.commands: list[dict[str, str]] = []
        self.menu_button = False

    def send_message(self, chat_id, text, *, reply_markup=None):
        self.messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})

    def send_photo(self, chat_id, jpeg_bytes, *, caption, reply_markup=None):
        self.photos.append({"chat_id": chat_id, "payload": jpeg_bytes, "caption": caption, "reply_markup": reply_markup})

    def send_video(
        self, chat_id, mp4_bytes, *, caption, reply_markup=None, width=None,
        height=None, duration=None, supports_streaming=None,
    ):
        self.videos.append({
            "chat_id": chat_id,
            "payload": mp4_bytes,
            "caption": caption,
            "reply_markup": reply_markup,
            "width": width,
            "height": height,
            "duration": duration,
            "supports_streaming": supports_streaming,
        })

    def answer_callback_query(self, callback_query_id, *, text, show_alert=False):
        self.answers.append({"id": callback_query_id, "text": text, "show_alert": show_alert})

    def set_my_commands(self, commands):
        self.commands = commands

    def set_commands_menu_button(self):
        self.menu_button = True


class _PollingNotifier(_Notifier):
    def __init__(self) -> None:
        super().__init__()
        self.polled = threading.Event()

    def get_webhook_info(self):
        return {"result": {"url": ""}}

    def get_updates(self, *, offset, timeout_seconds=20):
        self.polled.set()
        time.sleep(0.01)
        return []


class TelegramCommandRouterTests(unittest.TestCase):
    chat_viewer = "101"
    chat_operator = "202"

    def test_position_text_commands_move_verify_and_enforce_permission(self):
        pose = {"angle": 50, "elevation": 60}
        def read(_device):
            return {"success": True, "state": {"connected": True, "position": {
                **pose, "ret": 0, "updated_at": datetime.now(timezone.utc).isoformat()}}}
        def move(_device, **target):
            pose.update(target)
            return {"success": True}
        adapter = SimpleNamespace(device=lambda: "test-camera", read=read, move=Mock(side_effect=move))
        self.router.position_presets = PositionPresetService(adapter, self.root / "positions.json")
        self.router.handle_message_update(self._message(self.chat_operator, "儲存位置"))
        self._drain()
        pose.update(angle=30, elevation=0)
        self.router.handle_message_update(self._message(self.chat_operator, "位置一"))
        self._drain()
        self.assertEqual(pose, {"angle": 50, "elevation": 60})
        self.assertIn("座標讀回一致", self.notifier.messages[-1]["text"])
        self.assertEqual(adapter.move.call_count, 1)
        self.router.handle_message_update(self._message(self.chat_viewer, "/pos1"))
        self._drain()
        self.assertEqual(adapter.move.call_count, 1)
        self.assertIn("沒有權限", self.notifier.messages[-1]["text"])

    def test_compact_settings_row_and_feedback_one_click_toggle(self):
        rows = self.router._reply_keyboard(self.chat_viewer)["keyboard"]
        self.assertIn(["回饋", "通知", "冷卻"], [[item["text"] for item in row] for row in rows])
        for expected in (True, False):
            self.router.handle_message_update(self._message(self.chat_viewer, "回饋"))
            self._drain()
            self.assertEqual(self.cat_preferences.feedback_enabled(self.chat_viewer), expected)

    def test_numeric_notification_and_cooldown_inputs_are_private_and_scoped(self):
        self.router.handle_message_update(self._message(self.chat_viewer, "通知"))
        self._drain()
        markup = self.notifier.messages[-1]["reply_markup"]
        self.assertIn("你的貓咪事件通知已開啟", self.notifier.messages[-1]["text"])
        self.assertEqual([item["text"] for item in markup["inline_keyboard"][0]], ["開啟通知", "關閉通知"])
        self.assertFalse(self.router.handle_message_update(self._message(self.chat_operator, "2")))
        self.router.handle_message_update(self._message(self.chat_viewer, "2"))
        self._drain()
        self.assertTrue(self.cat_preferences.status(self.chat_viewer)["muted"])
        self.assertFalse(self.cat_preferences.status(self.chat_operator)["muted"])
        self.router.handle_message_update(self._message(self.chat_viewer, "冷卻"))
        self._drain()
        self.router.handle_message_update(self._message(self.chat_viewer, "15"))
        self._drain()
        self.assertEqual(self.cat_preferences.status(self.chat_viewer)["cooldown_minutes"], 15)
        self.assertFalse(self.router.handle_message_update(self._message(self.chat_viewer, "99")))

    def test_pending_settings_can_cancel_expire_and_be_interrupted(self):
        for text in ("通知", "冷卻"):
            self.router.handle_message_update(self._message(self.chat_viewer, text))
            self._drain()
            self.router.handle_message_update(self._message(self.chat_viewer, "取消"))
            self._drain()
            self.assertFalse(self.router.handle_message_update(self._message(self.chat_viewer, "2")))
        self.router.handle_message_update(self._message(self.chat_viewer, "冷卻"))
        self._drain()
        self.router._preference_input[self.chat_viewer] = ("cooldown", 0)
        self.router.handle_message_update(self._message(self.chat_viewer, "30"))
        self._drain()
        self.assertEqual(self.cat_preferences.status(self.chat_viewer)["cooldown_minutes"], 0)
        self.assertIn("過期", self.notifier.messages[-1]["text"])

    def test_notification_inline_callback_and_position_role_gate(self):
        self.cat_preferences.mute_hours(self.chat_viewer, 2)
        callback = {"id": "test-preference", "data": "mkpref:notifications:on",
                    "from": {"id": 101}, "message": {"chat": {"id": 101, "type": "private"}}}
        self.assertTrue(self.router.handle_callback_update(callback, update_id=9999))
        self._drain()
        self.assertFalse(self.cat_preferences.status(self.chat_viewer)["muted"])
        self.router.position_presets = Mock()
        self.router.handle_message_update(self._message(self.chat_viewer, "儲存位置"))
        self._drain()
        self.router.position_presets.save_next.assert_not_called()
        self.assertIn("沒有權限", self.notifier.messages[-1]["text"])

    def test_cd_command_is_per_authorized_user_and_persistent(self):
        preferences = self.router.cat_notification_preferences
        self.router.handle_message_update(self._message(self.chat_viewer, "/cd 15"))
        self._drain()
        self.assertIn("每 15 分鐘最多一次", self.notifier.messages[-1]["text"])
        self.assertEqual(preferences.status(self.chat_viewer)["cooldown_minutes"], 15)
        self.assertEqual(preferences.status(self.chat_operator)["cooldown_minutes"], 0)
        self.router.handle_message_update(self._message(self.chat_viewer, "/cd"))
        self._drain()
        self.assertIn("每 15 分鐘最多一次", self.notifier.messages[-1]["text"])
        for invalid in ("/cd -1", "/cd 1.5", "/cd 10081", "/cd 15 202"):
            self.router.handle_message_update(self._message(self.chat_viewer, invalid))
            self._drain()
            self.assertEqual(preferences.status(self.chat_viewer)["cooldown_minutes"], 15)
        self.router.handle_message_update(self._message("909090", "/cd 1"))
        self._drain()
        self.assertIn("沒有權限", self.notifier.messages[-1]["text"])
        self.assertEqual(preferences.status("909090")["cooldown_minutes"], 0)
        self.router.handle_message_update(self._message(self.chat_viewer, "/cd 0"))
        self._drain()
        self.assertEqual(preferences.status(self.chat_viewer)["cooldown_minutes"], 0)

    def test_reply_keyboard_is_persistent_and_role_filtered(self) -> None:
        for chat_id, command, control_visible in (
            (self.chat_viewer, "/start", False),
            (self.chat_operator, "/start", True),
        ):
            self.router.handle_message_update(self._message(chat_id, command))
            self._drain()
            markup = self.notifier.messages[-1]["reply_markup"]
            labels = [item["text"] for row in markup["keyboard"] for item in row]
            self.assertTrue(markup["is_persistent"])
            self.assertTrue(markup["resize_keyboard"])
            self.assertFalse(markup["one_time_keyboard"])
            self.assertIn("📷 即時截圖", labels)
            self.assertEqual("🔄 重啟監控" in labels, control_visible)
            self.assertEqual("🔒 隱私模式" in labels, control_visible)

    def test_button_labels_use_existing_authorization(self) -> None:
        self.router.handle_message_update(self._message("909090", "📷 即時截圖"))
        self.router.handle_message_update(self._message(self.chat_viewer, "🔒 隱私模式"))
        self._drain()
        self.controller.create_snapshot.assert_not_called()
        self.assertIn("沒有權限", self.notifier.messages[-1]["text"])
        self.router.handle_message_update(self._message(self.chat_viewer, "📊 系統狀態"))
        self._drain()
        self.assertIn("即時狀態", self.notifier.messages[-1]["text"])

    def test_unknown_start_keyboard_exposes_only_own_id(self) -> None:
        self.router.handle_message_update(self._message("909090", "🆔 我的 Chat ID"))
        self._drain()
        self.assertEqual(self.notifier.messages[-1]["reply_markup"]["keyboard"],
                         [[{"text": "🆔 我的 Chat ID"}]])
        self.assertNotIn("909090", self.router.recipients)

    def test_start_returns_only_private_senders_id_without_granting_access(self) -> None:
        stranger = "909090"
        before = dict(self.router.recipients)
        self.router.handle_message_update(self._message(stranger, "/start"))
        self._drain()
        self.assertEqual(len(self.notifier.messages), 1)
        self.assertEqual(self.notifier.messages[0]["chat_id"], stranger)
        self.assertIn(f"Chat ID：{stranger}", self.notifier.messages[0]["text"])
        self.assertIn("不會自動取得", self.notifier.messages[0]["text"])
        self.assertEqual(self.router.recipients, before)
        self.router.handle_message_update(self._message(stranger, "/snapshot"))
        self._drain()
        self.controller.create_snapshot.assert_not_called()

    def test_start_ignores_group_spoofed_sender_and_bot(self) -> None:
        messages = [
            self._message("909090", "/start", chat_type="group"),
            self._message("909090", "/start", sender_id="808080"),
            self._message("909090", "/start"),
        ]
        messages[-1]["from"]["is_bot"] = True
        for message in messages:
            self.router.handle_message_update(message)
        self._drain()
        self.assertEqual(self.notifier.messages, [])

    def test_start_uses_existing_poller_and_persisted_offset(self) -> None:
        poller = TelegramFeedbackPoller(
            self.notifier, self.store, [self.chat_viewer], command_router=self.router,
        )
        poller._process_update(500, {"message": self._message("909090", "/start")})
        self._drain()
        self.assertEqual(len(self.notifier.messages), 1)
        self.assertEqual(self.store.telegram_update_offset(), 501)

    def test_delivery_receipt_logs_only_safe_metadata(self) -> None:
        logs = []
        self.router.logger = logs.append
        receipt = TelegramSendResult("9876543210", 903)
        self.assertIs(self.router._retry_send(lambda: receipt, kind="photo"), receipt)
        self.assertEqual(len(logs), 1)
        self.assertIn("api_ok=true message_id=903", logs[0])
        self.assertIn("kind=photo", logs[0])
        self.assertNotIn(receipt.chat_id, logs[0])

    def test_missing_receipt_cannot_be_logged_as_verified(self) -> None:
        logs = []
        self.router.logger = logs.append
        for receipt in (None, TelegramSendResult("9876543210", None), TelegramSendResult("9876543210", True)):
            self.router._retry_send(lambda selected=receipt: selected)
        self.assertEqual(len(logs), 3)
        self.assertTrue(all("unverified" in item for item in logs))
        self.assertFalse(any("api_ok=true" in item for item in logs))

    def test_delivery_retry_logs_verified_receipt_once(self) -> None:
        logs = []
        self.router.logger = logs.append
        operation = Mock(side_effect=[RuntimeError("secret-do-not-log"), TelegramSendResult("9876543210", 904)])
        self.router._retry_send(operation, kind="video")
        self.assertEqual(operation.call_count, 2)
        self.assertEqual(len(logs), 1)
        self.assertIn("attempts=2", logs[0])
        self.assertNotIn("secret-do-not-log", logs[0])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = SQLiteStore(self.root / "commands.sqlite3")
        self.addCleanup(self.store.close)
        self.notifier = _Notifier()
        self.dispatcher = NotificationDispatcher(max_pending=16)
        self.addCleanup(lambda: self.dispatcher.shutdown(wait=True))
        self.jpeg = self.root / "latest.jpg"
        self.jpeg.write_bytes(b"\xff\xd8\xfftest")
        self.mp4 = self.root / "recent.mp4"
        self.mp4.write_bytes(b"\x00\x00\x00\x18ftypisomtest")
        self.controller = Mock()
        self.controller.create_snapshot.return_value = self.jpeg
        self.controller.get_recent_clip.return_value = SimpleNamespace(
            path=self.mp4, status="completed", actual_duration_seconds=14.8,
        )
        self.controller.get_status.return_value = {
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "metrics": {
                "source": {
                    "status": "running", "resolution": "640×360", "fps": 5,
                    "last_frame_at": datetime.now(timezone.utc).isoformat(),
                },
                "ai": {"model": "yolo11n.pt", "device": "cpu"},
                "ring_buffer": {"seconds": 15.0},
                "telegram": {"enabled": True},
            },
        }
        self.network = Mock()
        self.network.snapshot.return_value = {
            "lan_access_enabled": True,
            "urls": [
                "http://192.168.31.150:8000",
                "http://127.0.0.1:8000",
                "http://0.0.0.0:8000",
                "http://169.254.1.2:8000",
            ],
        }
        self.host_restart = Mock(return_value={"status": "restarting"})
        self.cat_preferences = CatNotificationPreferences(self.root / "cat-preferences.json")
        self.router = TelegramCommandRouter(
            notifier=self.notifier,
            dispatcher=self.dispatcher,
            controller=self.controller,
            store=self.store,
            recipients={self.chat_viewer: "viewer", self.chat_operator: "operator"},
            dashboard_network=self.network,
            tailscale_status=lambda: {"remote_dashboard": {"url": "https://meow.ts.net/"}},
            host_restart=self.host_restart,
            cat_notification_preferences=self.cat_preferences,
            live_seconds=15,
            ring_retention_seconds=60,
        )
        self.addCleanup(lambda: self.router.close(wait=True))

    @staticmethod
    def _message(chat_id: str, text: str, *, chat_type: str = "private", sender_id: str | None = None):
        return {
            "chat": {"id": int(chat_id), "type": chat_type},
            "from": {"id": int(sender_id or chat_id)},
            "text": text,
        }

    def _drain(self) -> None:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if self.dispatcher.pending_count == 0 and self.router.media_dispatcher.pending_count == 0:
                return
            time.sleep(0.01)
        self.fail("Telegram 命令佇列未在測試期限內排空。")

    def test_cat_notification_commands_are_authorized_per_chat_and_persistent(self) -> None:
        self.router.handle_message_update(self._message("909090", "/catnotify off"))
        self._drain()
        self.assertTrue(self.cat_preferences.should_send("101", "cat_detected"))

        self.router.handle_message_update(self._message("101", "/catnotify off"))
        self._drain()
        self.assertFalse(self.cat_preferences.should_send("101", "cat_detected"))
        self.assertTrue(self.cat_preferences.should_send("202", "cat_detected"))
        self.assertTrue(self.cat_preferences.should_send("101", "possible_vomit"))
        self.assertNotIn("101", (self.root / "cat-preferences.json").read_text(encoding="utf-8"))
        reopened = CatNotificationPreferences(self.root / "cat-preferences.json")
        self.assertFalse(reopened.should_send("101", "cat_detected"))
        self.router.handle_message_update(self._message("101", "/catnotify on"))
        self._drain()
        self.assertTrue(reopened.status("202")["enabled"])
        self.assertTrue(self.cat_preferences.should_send("101", "cat_detected"))

    def test_feedback_opt_in_is_individual_and_does_not_change_alerts(self) -> None:
        self.router.handle_message_update(self._message("101", "/feedback on"))
        self._drain()
        self.assertTrue(self.cat_preferences.feedback_enabled("101"))
        self.assertFalse(self.cat_preferences.feedback_enabled("202"))
        self.assertTrue(self.cat_preferences.should_send("101", "cat_detected"))
        self.router.handle_message_update(self._message("202", "/feedback status"))
        self._drain()
        self.assertIn("未參與", str(self.notifier.messages[-1]["text"]))
        self.router.handle_message_update(self._message("101", "/feedback off"))
        self._drain()
        self.assertFalse(self.cat_preferences.feedback_enabled("101"))
        self.assertTrue(self.cat_preferences.should_send("101", "cat_detected"))

    def test_mute_hours_validates_bounds_and_expires_without_restart(self) -> None:
        for command in ("/mute", "/mute 0", "/mute 169", "/mute abc"):
            self.router.handle_message_update(self._message("101", command))
            self._drain()
            self.assertIn("1～168", str(self.notifier.messages[-1]["text"]))
        self.router.handle_message_update(self._message("101", "/mute 2"))
        self._drain()
        self.assertFalse(self.cat_preferences.should_send("101", "cat_detected"))
        self.assertTrue(self.cat_preferences.should_send("202", "cat_detected"))
        self.router.handle_message_update(self._message("101", "/catnotify status"))
        self._drain()
        self.assertIn("靜音至", str(self.notifier.messages[-1]["text"]))
        self.router.handle_message_update(self._message("101", "/unmute"))
        self._drain()
        self.assertTrue(self.cat_preferences.should_send("101", "cat_detected"))

    def test_sleep_command_saves_and_clears_personal_schedule(self):
        for command in ("/sleep 24-8", "/sleep 8-8", "/sleep abc"):
            self.router.handle_message_update(self._message("101", command))
            self._drain()
            self.assertIn("用法", str(self.notifier.messages[-1]["text"]))
        self.router.handle_message_update(self._message("101", "/sleep 23-8"))
        self._drain()
        self.assertEqual(self.cat_preferences.status("101")["sleep_window"],
                         {"start_hour": 23, "end_hour": 8})
        self.assertIsNone(self.cat_preferences.status("202")["sleep_window"])
        self.assertIn("Host 電腦當地時間", str(self.notifier.messages[-1]["text"]))
        self.router.handle_message_update(self._message("101", "/sleep"))
        self._drain()
        self.assertIn("23:00 至 08:00", str(self.notifier.messages[-1]["text"]))
        self.router.handle_message_update(self._message("101", "/sleep off"))
        self._drain()
        self.assertIsNone(self.cat_preferences.status("101")["sleep_window"])

    def test_registers_persistent_menu_and_role_gates_privacy_commands(self):
        self.router.register_menu()
        self.assertEqual({item["command"] for item in self.notifier.commands}, {
            "start", "snapshot", "live", "status", "today", "hide", "show", "reset", "home",
            "catnotify", "cd", "mute", "sleep", "unmute", "feedback", "notify", "savepos", "pos1", "pos2",
        })
        self.assertTrue(self.notifier.menu_button)

        self.router.handle_message_update(self._message(self.chat_viewer, "/hide"))
        self.router.handle_message_update(self._message("999", "/status"))
        self._drain()
        self.assertEqual(len(self.notifier.messages), 2)
        self.assertTrue(all("沒有權限" in str(item["text"]) for item in self.notifier.messages))
        self.controller.restart_monitor.assert_not_called()

    def test_unregistered_private_command_is_denied_without_exposing_identifiers(self):
        logs = []
        self.router.logger = logs.append
        self.router.handle_message_update(self._message("909090", "/snapshot"))
        self._drain()
        self.controller.create_snapshot.assert_not_called()
        self.assertEqual(len(self.notifier.messages), 1)
        self.assertIn("沒有權限", self.notifier.messages[0]["text"])
        self.assertNotIn("909090", self.notifier.messages[0]["text"])
        self.assertNotIn("909090", "\n".join(logs))
        self.assertTrue(any("TELEGRAM_UNAUTHORIZED" in line for line in logs))

    def test_unregistered_group_spoofed_sender_and_bot_receive_no_reply(self):
        updates = [
            self._message("909090", "/snapshot", chat_type="group"),
            self._message("909090", "/snapshot", sender_id="808080"),
            self._message("909090", "/snapshot"),
        ]
        updates[-1]["from"]["is_bot"] = True
        for message in updates:
            self.router.handle_message_update(message)
        self._drain()
        self.assertEqual(self.notifier.messages, [])
        self.controller.create_snapshot.assert_not_called()

    def test_snapshot_rejects_stale_cache_and_disconnected_source(self):
        for state, timestamp in (
            ("running", "2000-01-01T00:00:00+00:00"),
            ("disconnected", datetime.now(timezone.utc).isoformat()),
            ("running", None),
            ("running", datetime.now(timezone.utc).replace(tzinfo=None).isoformat()),
        ):
            with self.subTest(source_state=state, frame_at=timestamp):
                source = self.controller.get_status.return_value["metrics"]["source"]
                source.update(status=state, last_frame_at=timestamp)
                self.router.handle_message_update(self._message(self.chat_viewer, "/snapshot"))
                self._drain()
                self.assertIn("來源未連線或最新影格已過期", self.notifier.messages[-1]["text"])
                self.assertEqual(self.notifier.photos, [])
        self.controller.create_snapshot.assert_not_called()
        self.controller.start_monitor.assert_not_called()

    def test_status_uses_current_host_and_go2rtc_health_providers(self):
        self.router.status_service.go2rtc_status = lambda: SimpleNamespace(healthy=True)
        self.router.status_service.dashboard_health = lambda: True
        self.router.handle_message_update(self._message(self.chat_viewer, "/status"))
        self._drain()
        text = str(self.notifier.messages[-1]["text"])
        self.assertIn("20 秒內有新影格", text)
        self.assertIn("640×360", text)
        self.assertIn("go2rtc：🟢 API 與 RTSP loopback 可用", text)
        self.assertIn(f"PID {os.getpid()}", text)
        self.assertIn("執行 0 小時", text)
        self.assertIn("192.168.31.150", text)
        self.assertIn("系統：CPU", text)
        self.assertIn("儲存空間：可用", text)
        self.assertIn("http://192.168.31.150:8000", text)
        self.assertNotIn("127.0.0.1", text)
        self.assertNotIn("0.0.0.0", text)

    def test_status_and_startup_do_not_claim_connected_from_recent_disconnected_cache(self):
        source = self.controller.get_status.return_value["metrics"]["source"]
        source["last_frame_at"] = datetime.now(timezone.utc).isoformat()
        for state in ("disconnected", "stopped", "reconnecting", "error", "unknown"):
            with self.subTest(state=state):
                source["status"] = state
                self.assertFalse(self.router.status_service.snapshot()["camera"]["connected"])
                for label, text in (("畫面", self.router.status_service.command_text()),
                                    ("Camera", self.router.status_service.startup_notice())):
                    self.assertIn(f"{label}：⚪", text)
                    self.assertNotIn(f"{label}：🟢", text)

    def test_status_camera_requires_active_source_and_valid_recent_timestamp(self):
        source = self.controller.get_status.return_value["metrics"]["source"]
        for state in ("running", "connected", "streaming"):
            with self.subTest(state=state):
                source["status"] = state
                source["last_frame_at"] = datetime.now(timezone.utc).isoformat()
                self.assertTrue(self.router.status_service.snapshot()["camera"]["connected"])
                for invalid in (
                    None, "invalid", datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                    (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat(),
                    (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
                ):
                    source["last_frame_at"] = invalid
                    self.assertFalse(self.router.status_service.snapshot()["camera"]["connected"])

    def test_startup_notice_and_status_use_the_same_live_snapshot_service(self):
        service = self.router.status_service
        service.dashboard_health = lambda: True
        service.source_type = "rtsp"
        service.source_configured = True
        command_status = service.command_text()
        startup_status = service.startup_notice()
        self.assertIn("狀態：🟢 正常", command_status)
        self.assertIn("Dashboard：🟢 Healthy", startup_status)
        self.assertIn("20 秒內有新影格", command_status)
        self.assertIn("20 秒內有新影格", startup_status)
        self.assertIn("640×360", command_status)
        self.assertIn("rtsp", startup_status)
        self.assertNotIn("127.0.0.1", startup_status)
        self.assertNotIn("0.0.0.0", startup_status)

    def test_loopback_status_shows_lan_candidate_without_claiming_remote_availability(self):
        self.network.snapshot.return_value = {
            "lan_access_enabled": True,
            "bind_host": "127.0.0.1",
            "urls": ["http://192.168.31.150:8123"],
            "lan_candidate_urls": ["http://192.168.31.150:8123", "http://0.0.0.0:8123"],
        }
        service = self.router.status_service
        service.tailscale_status = dict
        text = service.command_text()
        self.assertIn("內網候選：http://192.168.31.150:8123", text)
        self.assertIn("僅本機監聽", text)
        self.assertIn("Tailscale：尚無已驗證", text)
        self.assertNotIn("0.0.0.0", text)
        self.assertEqual(service.snapshot()["dashboard"]["urls"], [])

    def test_status_marks_dashboard_unhealthy_when_live_health_probe_fails(self):
        self.router.status_service.dashboard_health = lambda: False
        self.router.handle_message_update(self._message(self.chat_viewer, "/status"))
        self._drain()
        text = str(self.notifier.messages[-1]["text"])
        self.assertIn("狀態：🔴 無法使用", text)
        self.assertNotIn("狀態：🟢 正常", text)

    def test_privacy_commands_fail_closed_without_adapter(self):
        self.router.handle_message_update(self._message(self.chat_operator, "/hide"))
        self.router.handle_message_update(self._message(self.chat_operator, "/show"))
        self._drain()
        self.assertIn("沒有移動攝影機", str(self.notifier.messages[0]["text"]))
        self.assertIn("沒有移動攝影機", str(self.notifier.messages[1]["text"]))

    def test_status_privacy_requires_verified_camera_readback(self):
        service = self.router.status_service
        service.privacy_adapter_configured = True
        service.privacy_status = lambda: {"verified": True, "privacy_mode": True}
        self.assertIn("Privacy：隱私位置已確認", service.command_text())
        service.privacy_status = lambda: {"verified": True, "privacy_mode": False}
        self.assertIn("Privacy：正常位置已確認", service.command_text())
        service.privacy_status = lambda: {"verified": False, "privacy_mode": True}
        self.assertIn("Privacy：Adapter 已設定，狀態尚未確認", service.command_text())

    def test_privacy_commands_report_only_verified_adapter_success(self):
        calls: list[str] = []

        class Adapter:
            mode = False

            def read_privacy_state(self):
                return {"verified": True, "privacy_mode": self.mode}

            def hide(self):
                calls.append("hide")
                self.mode = True
                return {"success": True, "message_zh_tw": "sent"}

            def show(self):
                calls.append("show")
                self.mode = False
                return {"success": True, "message_zh_tw": "sent"}

        self.router.privacy_service = PrivacyService(Adapter())
        self.router.handle_message_update(self._message(self.chat_operator, "/hide"))
        self.router.handle_message_update(self._message(self.chat_operator, "/show"))
        self._drain()
        self.assertEqual(calls, ["hide", "show"])
        self.assertIn("隱私模式已啟用", str(self.notifier.messages[0]["text"]))
        self.assertIn("已恢復攝影機畫面", str(self.notifier.messages[1]["text"]))
        self.assertNotIn("sent", str(self.notifier.messages))

    def test_privacy_service_requires_explicit_adapter_success_and_redacts_adapter_text(self):
        class Adapter:
            mode = False

            def read_privacy_state(self):
                return {"verified": True, "privacy_mode": self.mode}

            def hide(self):
                self.mode = True
                return {"success": True, "message_zh_tw": "token=should-not-leak"}

            def show(self):
                return {"success": False, "message_zh_tw": "sent but unverified"}

        privacy = PrivacyService(Adapter())
        self.assertTrue(privacy.hide()["success"])
        self.assertNotIn("token", privacy.hide()["message_zh_tw"])
        self.assertFalse(privacy.show()["success"])

    def test_snapshot_uses_existing_latest_frame_without_starting_source(self):
        self.router.handle_message_update(self._message(self.chat_viewer, "/snapshot"))
        self._drain()
        self.controller.create_snapshot.assert_called_once_with()
        self.controller.start_monitor.assert_not_called()
        self.assertEqual(len(self.notifier.photos), 1)
        self.assertTrue(bytes(self.notifier.photos[0]["payload"]).startswith(b"\xff\xd8\xff"))

    def test_live_uses_existing_ring_buffer_and_separate_bounded_media_queue(self):
        compatible = MediaValidationResult(
            self.mp4, True, True, False, 14.8, None, "h264", 640, 360,
            "yuv420p", "mov,mp4", True,
        )
        with patch("meowkeeper.core.notifications.telegram_commands.validate_media", return_value=compatible):
            self.router.handle_message_update(self._message(self.chat_viewer, "/live"))
            self._drain()
        self.controller.get_recent_clip.assert_called_once_with(15.0, 0.0)
        self.controller.start_monitor.assert_not_called()
        self.assertEqual(len(self.notifier.videos), 1)
        self.assertIn("H.264（已驗證）", str(self.notifier.videos[0]["caption"]))
        self.assertEqual(self.notifier.videos[0]["width"], 640)
        self.assertEqual(self.notifier.videos[0]["height"], 360)
        self.assertTrue(self.notifier.videos[0]["supports_streaming"])

    def test_live_transcodes_incompatible_clip_before_sending(self):
        source_validation = MediaValidationResult(
            self.mp4, True, True, False, 14.8, None, "hevc", 1920, 1080,
            "yuv420p", "mov,mp4", True,
        )
        compatible_path = self.root / "telegram-live-h264.mp4"
        compatible_path.write_bytes(b"\x00\x00\x00\x18ftypisomtest")
        compatible_validation = MediaValidationResult(
            compatible_path, True, True, False, 14.8, None, "h264", 640, 360,
            "yuv420p", "mov,mp4", True,
        )

        def transcode(source, destination):
            destination.write_bytes(compatible_path.read_bytes())
            return ClipAssemblyResult(destination, "completed", 14.8, False, validation=compatible_validation)

        self.controller.transcode_clip_for_telegram.side_effect = transcode
        with patch("meowkeeper.core.notifications.telegram_commands.validate_media", return_value=source_validation):
            self.router.handle_message_update(self._message(self.chat_viewer, "/live"))
            self._drain()

        self.controller.transcode_clip_for_telegram.assert_called_once()
        self.assertEqual(len(self.notifier.videos), 1)
        sent = self.notifier.videos[0]
        self.assertEqual(sent["payload"], compatible_path.read_bytes())
        self.assertEqual((sent["width"], sent["height"]), (640, 360))
        self.assertEqual(sent["duration"], 14.8)
        self.assertIn("H.264（已驗證）", sent["caption"])
        self.assertTrue(sent["supports_streaming"])

    def test_non_private_viewer_cannot_read_snapshot_or_start_media_work(self):
        self.router.handle_message_update(self._message(self.chat_viewer, "/snapshot", chat_type="group"))
        self.router.handle_message_update(self._message(self.chat_viewer, "/live", chat_type="group"))
        self._drain()
        self.controller.create_snapshot.assert_not_called()
        self.controller.get_recent_clip.assert_not_called()
        self.assertEqual(self.notifier.photos, [])
        self.assertEqual(self.notifier.videos, [])

    def test_empty_ring_buffer_returns_unavailable_without_starting_source(self):
        self.controller.get_recent_clip.return_value = SimpleNamespace(path=None, status="empty")
        self.router.handle_message_update(self._message(self.chat_viewer, "/live"))
        self._drain()
        self.controller.get_recent_clip.assert_called_once_with(15.0, 0.0)
        self.controller.start_monitor.assert_not_called()
        self.assertIn("沒有啟動新的攝影機連線", str(self.notifier.messages[-1]["text"]))
        self.assertEqual(self.notifier.videos, [])

    def test_home_only_returns_valid_lan_or_tailscale_urls(self):
        self.router.handle_message_update(self._message(self.chat_viewer, "/home"))
        self._drain()
        markup = self.notifier.messages[-1]["reply_markup"]
        urls = [row[0]["url"] for row in markup["inline_keyboard"]]
        self.assertEqual(urls, ["http://192.168.31.150:8000", "https://meow.ts.net/"])

    def test_command_message_uses_the_same_persistent_update_offset(self):
        poller = TelegramFeedbackPoller(
            self.notifier, self.store, [self.chat_viewer], command_router=self.router,
        )
        poller._process_update(76, {
            "update_id": 76,
            "message": self._message(self.chat_viewer, "/status"),
        })
        self.assertEqual(self.store.telegram_update_offset(), 77)
        self._drain()
        self.assertIn("即時狀態", str(self.notifier.messages[-1]["text"]))

    def test_single_poller_syncs_command_menu_after_webhook_check(self):
        notifier = _PollingNotifier()
        router = TelegramCommandRouter(
            notifier=notifier,
            dispatcher=self.dispatcher,
            controller=self.controller,
            store=self.store,
            recipients={self.chat_viewer: "viewer"},
        )
        self.addCleanup(lambda: router.close(wait=True))
        poller = TelegramFeedbackPoller(
            notifier, self.store, [self.chat_viewer], command_router=router,
        )
        poller.start()
        try:
            self.assertTrue(notifier.polled.wait(2.0))
        finally:
            poller.close(wait=True)
        self.assertEqual(len(notifier.commands), len(TelegramCommandRouter.COMMANDS))
        self.assertTrue(notifier.menu_button)

    def test_today_counts_detector_events_and_keeps_behavior_durations_unavailable(self):
        now = datetime.now(timezone.utc)
        for event_id, origin in (("today-real", "detector"), ("today-sim", "simulated")):
            self.store.create_media_event(
                event_id=event_id,
                event_type="cat_detected",
                trigger_at=now,
                source_id="test-source",
                track_id=None,
                pet_id="unknown",
                confidence=0.8,
                requested_pre_seconds=0,
                requested_post_seconds=0,
                trigger_origin=origin,
            )
        self.router.handle_message_update(self._message(self.chat_viewer, "/today"))
        self._drain()
        text = str(self.notifier.messages[-1]["text"])
        self.assertIn("真實 detector 貓咪告警：1 次", text)
        self.assertIn("模擬告警（不納入真實偵測）：1 次", text)
        self.assertIn("尚未完成家庭實拍驗收", text)
        self.assertNotIn("睡眠：0", text)

    def test_today_uses_persisted_sampling_totals_and_preserves_unknown_behaviors(self):
        self.store.record_monitoring_batch(
            batch_id=str(uuid4()), day=datetime.now().astimezone().date().isoformat(),
            monitoring_seconds=120, analyzed_seconds=90, cat_visible_seconds=60,
        )
        self.router.handle_message_update(self._message(self.chat_viewer, "/today"))
        self._drain()
        text = str(self.notifier.messages[-1]["text"])
        self.assertIn("已保存有效影格取樣時間：" + self.router._duration(120), text)
        self.assertIn("貓咪偵測出現取樣時間：" + self.router._duration(60), text)
        self.assertIn("不等同玩耍活動", text)
        self.assertNotIn("睡眠：0", text)

    def test_reset_requires_operator_confirmation_then_health_result_is_persisted(self):
        self.router.handle_message_update(self._message(self.chat_viewer, "/reset"))
        self._drain()
        self.assertEqual(self.host_restart.call_count, 0)
        self.assertIn("沒有權限", str(self.notifier.messages[-1]["text"]))

        self.router.handle_message_update(self._message(self.chat_operator, "/reset"))
        self._drain()
        markup = self.notifier.messages[-1]["reply_markup"]
        callback_data = markup["inline_keyboard"][0][0]["callback_data"]
        request_id = callback_data.rsplit(":", 1)[1]
        callback = {
            "id": "callback-reset",
            "data": callback_data,
            "from": {"id": int(self.chat_operator)},
            "message": {"message_id": 7, "chat": {"id": int(self.chat_operator), "type": "private"}},
        }
        poller = TelegramFeedbackPoller(
            self.notifier, self.store, [self.chat_operator], command_router=self.router,
        )
        poller._process_update(77, {"update_id": 77, "callback_query": callback})
        self._drain()
        self.assertEqual(self.store.telegram_update_offset(), 78)
        self.host_restart.assert_called_once_with()
        self.assertEqual(self.store.pending_telegram_restart_requests()[0]["request_id"], request_id)

        self.router.report_pending_restart_results()
        self._drain()
        self.assertIn("Dashboard /healthz 已通過", str(self.notifier.messages[-1]["text"]))
        self.assertEqual(self.store.pending_telegram_restart_requests(), [])

    def test_reset_restart_failure_is_persisted_and_reported(self):
        self.host_restart.return_value = {"status": "failed"}
        self.router.handle_message_update(self._message(self.chat_operator, "/reset"))
        self._drain()
        markup = self.notifier.messages[-1]["reply_markup"]
        callback_data = markup["inline_keyboard"][0][0]["callback_data"]
        callback = {
            "id": "callback-reset-failed",
            "data": callback_data,
            "from": {"id": int(self.chat_operator)},
            "message": {"message_id": 8, "chat": {"id": int(self.chat_operator), "type": "private"}},
        }
        poller = TelegramFeedbackPoller(
            self.notifier, self.store, [self.chat_operator], command_router=self.router,
        )
        poller._process_update(78, {"update_id": 78, "callback_query": callback})
        self._drain()
        row = self.store.connection.execute(
            "SELECT status FROM telegram_restart_requests WHERE request_id = ?",
            (callback_data.rsplit(":", 1)[1],),
        ).fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertIn("Host 重啟未啟動", str(self.notifier.messages[-1]["text"]))


if __name__ == "__main__":
    unittest.main()
