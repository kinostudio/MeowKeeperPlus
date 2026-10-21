from __future__ import annotations

import hashlib
import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from meowkeeper.core.analytics import PerformanceMetrics
from meowkeeper.core.behavior.models import BehaviorEvent
from meowkeeper.core.event_semantics import SemanticFuture, SemanticResult
from meowkeeper.core.events.v02_pipeline import V02AnalysisPipeline, V02TrackResult
from meowkeeper.core.identity.pet_registry import PetRegistry
from meowkeeper.core.media.clip_assembler import ClipAssemblyResult
from meowkeeper.core.media.event_clip import EventClipRequest, EventClipService
from meowkeeper.core.media.media_validation import MediaValidationResult
from meowkeeper.core.notifications.cat_preferences import CatNotificationPreferences
from meowkeeper.core.notifications.dispatcher import NotificationDispatcher
from meowkeeper.core.notifications.telegram import TelegramSendResult
from meowkeeper.core.notifications.telegram_feedback import TelegramFeedbackPoller
from meowkeeper.core.tracking.base import BoundingBox, TrackObservation
from meowkeeper.domain.behavior import BehaviorState, MotionState
from meowkeeper.storage.database import SCHEMA_VERSION, SQLiteStore


class _FakeTelegram:
    def __init__(self) -> None:
        self.sent_videos: list[dict[str, object]] = []
        self.sent_photos: list[dict[str, object]] = []
        self.answers: list[dict[str, object]] = []
        self.sent_messages: list[dict[str, object]] = []
        self.edited_markups: list[dict[str, object]] = []

    def edit_message_reply_markup(self, chat_id, message_id, *, reply_markup):
        self.edited_markups.append({"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup})

    def edit_message_text(self, chat_id, message_id, text, *, reply_markup):
        self.edited_markups.append({"chat_id": chat_id, "message_id": message_id, "text": text, "reply_markup": reply_markup})

    def send_message(self, chat_id: str, text: str, *, reply_markup=None):
        message_id = 300 + len(self.sent_messages)
        self.sent_messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup,
                                   "message_id": message_id})
        return TelegramSendResult(chat_id, message_id)

    def send_video(
        self, chat_id: str, payload: bytes, *, caption: str, reply_markup=None,
        width=None, height=None, duration=None, supports_streaming=None,
    ):
        message_id = 100 + len(self.sent_videos)
        self.sent_videos.append({
            "chat_id": chat_id, "payload": payload, "caption": caption,
            "reply_markup": reply_markup, "message_id": message_id,
            "width": width, "height": height, "duration": duration,
            "supports_streaming": supports_streaming,
        })
        return TelegramSendResult(chat_id, message_id)

    def send_photo(self, chat_id: str, payload: bytes, *, caption: str, reply_markup=None):
        message_id = 200 + len(self.sent_photos)
        self.sent_photos.append({
            "chat_id": chat_id, "payload": payload, "caption": caption,
            "reply_markup": reply_markup, "message_id": message_id,
        })
        return TelegramSendResult(chat_id, message_id)

    def answer_callback_query(self, callback_query_id: str, *, text: str, show_alert: bool = False):
        self.answers.append({"id": callback_query_id, "text": text, "show_alert": show_alert})


class _PollingTelegram(_FakeTelegram):
    def __init__(self, updates: list[dict[str, object]], *, webhook_url: str = "") -> None:
        super().__init__()
        self.updates = list(updates)
        self.webhook_url = webhook_url
        self.get_updates_calls = 0

    def get_webhook_info(self):
        return {"result": {"url": self.webhook_url}}

    def get_updates(self, *, offset: int | None, timeout_seconds: int = 20):
        self.get_updates_calls += 1
        if self.updates:
            return [self.updates.pop(0)]
        time.sleep(0.01)
        return []


class TelegramFeedbackLoopTests(unittest.TestCase):
    chat_id = "123456789"

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        from unittest.mock import patch

        self._validation_patch = patch(
            "meowkeeper.core.media.event_clip.validate_media",
            side_effect=lambda path: MediaValidationResult(
                Path(path), True, True, False, 2.0,
                video_codec="h264", width=320, height=240, pixel_format="yuv420p",
                container_format="mov,mp4,m4a", mp4_faststart=True,
            ),
        )
        self._validation_patch.start()
        self.addCleanup(self._validation_patch.stop)
        self.store = SQLiteStore(self.root / "feedback.sqlite3")
        self.addCleanup(self.store.close)
        self.telegram = _FakeTelegram()
        self.preferences = CatNotificationPreferences(self.root / "feedback-prefs.json")
        self.preferences.set_feedback_enabled(self.chat_id, True)
        self.service = EventClipService(
            Mock(), Mock(), output_directory=self.root / "events", store=self.store,
            notifier=self.telegram, chat_ids=[self.chat_id], telegram_enabled=True,
            cat_notification_preferences=self.preferences,
            retry_sleep=lambda _seconds: None,
        )
        self.addCleanup(self.service.close)

    def _send_detector_alert(self, *, confidence: float = 0.72) -> tuple[str, int, dict[str, object]]:
        event_id = str(uuid4())
        triggered = datetime.now(timezone.utc)
        self.store.create_media_event(
            event_id=event_id, event_type="cat_detected", trigger_at=triggered,
            source_id="local-test", track_id=None, pet_id="unknown", confidence=confidence,
            requested_pre_seconds=5, requested_post_seconds=10,
            trigger_origin="detector", model_version="test-model", model_sha256="a" * 64,
            confidence_threshold=0.45,
        )
        clip = self.root / f"{event_id}.mp4"
        clip.write_bytes(b"local test video")
        request = EventClipRequest(
            event_id, triggered, "cat_detected", "local-test", confidence=confidence,
            screenshot_bytes=b"test-jpeg",
        )
        result = ClipAssemblyResult(
            clip, "completed", 2.0, True, size_bytes=clip.stat().st_size,
            sha256=hashlib.sha256(clip.read_bytes()).hexdigest(),
        )
        self.service._queue_telegram(request, result, ())
        self.assertTrue(self.service.wait_for_idle(timeout_seconds=3))
        message = next(item for item in reversed(self.telegram.sent_videos)
                       if item["chat_id"] == self.chat_id)
        self.assertIsNotNone(message["reply_markup"])
        self.assertEqual(self.store.get_media_event(event_id)["telegram_status"], "sent")
        return event_id, int(message["message_id"]), message

    def _callback(self, *, event_id: str, message_id: int, update_id: int, label: str,
                  chat_id: str | None = None, user_id: str | None = None, chat_type: str = "private") -> dict[str, object]:
        callback_chat = chat_id or self.chat_id
        callback_user = user_id or callback_chat
        return {
            "update_id": update_id,
            "callback_query": {
                "id": f"callback-{update_id}",
                "from": {"id": int(callback_user)},
                "message": {
                    "message_id": message_id,
                    "chat": {"id": int(callback_chat), "type": chat_type},
                },
                "data": f"cat:{event_id}:{label}",
            },
        }

    def test_only_opted_in_recipient_gets_buttons_and_opt_out_rejects_old_button(self):
        other_chat = "987654321"
        self.service.chat_ids = (self.chat_id, other_chat)
        event_id, message_id, _ = self._send_detector_alert()
        self.assertEqual([item["chat_id"] for item in self.telegram.sent_videos],
                         [self.chat_id, other_chat])
        self.assertIsNone(self.telegram.sent_videos[-1]["reply_markup"])
        self.assertTrue(self.preferences.should_send(other_chat, "cat_detected"))
        self.preferences.set_feedback_enabled(self.chat_id, False)
        poller = TelegramFeedbackPoller(self.telegram, self.store, [self.chat_id, other_chat],
                                        preferences=self.preferences)
        poller._process_update(490, self._callback(
            event_id=event_id, message_id=message_id, update_id=490, label="yes",
        ))
        self.assertEqual(self.store.cat_feedback_summary()["reviewed_events"], 0)
        self.assertTrue(self.telegram.answers[-1]["show_alert"])

    def test_detector_video_button_to_persisted_cat_and_not_cat_feedback(self):
        poller = TelegramFeedbackPoller(self.telegram, self.store, [self.chat_id], preferences=self.preferences)
        for update_id, label in ((501, "yes"), (502, "no")):
            event_id, message_id, message = self._send_detector_alert()
            buttons = message["reply_markup"]["inline_keyboard"][0]
            self.assertEqual([button["text"] for button in buttons], ["✅ 是貓咪", "❌ 不是貓咪"])
            self.assertEqual(buttons[0]["callback_data"], f"cat:{event_id}:yes")
            self.assertEqual(buttons[1]["callback_data"], f"cat:{event_id}:no")
            self.store.record_telegram_receipt(
                event_id, hashlib.sha256(self.chat_id.encode()).hexdigest(), "video", message_id,
            )
            poller._process_update(
                update_id,
                self._callback(event_id=event_id, message_id=message_id, update_id=update_id, label=label),
            )

        summary = self.store.cat_feedback_summary()
        self.assertEqual(summary["confirmed_cat"], 1)
        self.assertEqual(summary["confirmed_not_cat"], 1)
        self.assertEqual(summary["reviewed_events"], 2)
        self.assertEqual(summary["unreviewed_events"], 0)
        self.assertFalse(summary["model_score_is_calibrated_probability"])
        self.assertIsNotNone(summary["reviewed_alert_precision_wilson_95"])
        bucket = next(item for item in summary["confidence_score_buckets"] if item["range"] == "0.70-0.80")
        self.assertEqual((bucket["cat"], bucket["not_cat"], bucket["consensus"]), (1, 1, 2))
        self.assertEqual(bucket["reviewed_alert_precision"], 0.5)
        self.assertLess(bucket["precision_wilson_95"]["lower"], 0.5)
        self.assertGreater(bucket["precision_wilson_95"]["upper"], 0.5)
        self.assertTrue(all(answer["show_alert"] is False for answer in self.telegram.answers))
        self.assertIn("已記錄：是貓咪。", [answer["text"] for answer in self.telegram.answers])
        self.assertIn("已記錄：不是貓咪。", [answer["text"] for answer in self.telegram.answers])
        self.assertEqual(self.store.telegram_update_offset(), 503)

    def test_same_reviewer_can_correct_label_without_counting_duplicate_sample(self):
        event_id, message_id, _ = self._send_detector_alert()
        self.store.record_telegram_receipt(
            event_id, hashlib.sha256(self.chat_id.encode()).hexdigest(), "video", message_id,
        )
        poller = TelegramFeedbackPoller(self.telegram, self.store, [self.chat_id], preferences=self.preferences)
        for update_id, label in ((610, "yes"), (611, "no")):
            poller._process_update(
                update_id,
                self._callback(event_id=event_id, message_id=message_id, update_id=update_id, label=label),
            )
        summary = self.store.cat_feedback_summary()
        self.assertEqual(summary["reviewed_events"], 1)
        self.assertEqual(summary["confirmed_cat"], 0)
        self.assertEqual(summary["confirmed_not_cat"], 1)
        with self.store._connection_lock:
            history_count = self.store.connection.execute(
                "SELECT COUNT(*) FROM media_event_feedback_history WHERE event_id=?", (event_id,),
            ).fetchone()[0]
        self.assertEqual(history_count, 2)

    def test_confirmed_cat_opens_identity_behavior_feedback_and_keeps_history(self):
        registry = PetRegistry(self.root / "pets")
        pet = registry.register_pet("測試貓")
        event_id, message_id, _ = self._send_detector_alert()
        self.store.record_telegram_receipt(
            event_id, hashlib.sha256(self.chat_id.encode()).hexdigest(), "video", message_id,
        )
        poller = TelegramFeedbackPoller(self.telegram, self.store, [self.chat_id], pet_registry=registry,
                                        preferences=self.preferences)
        poller._process_update(
            801, self._callback(event_id=event_id, message_id=message_id, update_id=801, label="yes"),
        )
        self.assertEqual(len(self.telegram.sent_messages), 1)
        prompt = self.telegram.sent_messages[0]
        options = [item for row in prompt["reply_markup"]["inline_keyboard"] for item in row]
        pet_option = next(item for item in options if item["text"] == "🐱 測試貓")
        self.assertLessEqual(len(pet_option["callback_data"].encode()), 64)
        behavior_option = next(item for item in options if item["text"] == "進食")
        for update_id, option in ((802, pet_option), (803, behavior_option)):
            update = self._callback(event_id=event_id, message_id=prompt["message_id"],
                                    update_id=update_id, label="yes")
            update["callback_query"]["data"] = option["callback_data"]
            poller._process_update(update_id, update)
        with self.store._connection_lock:
            rows = self.store.connection.execute(
                "SELECT dimension,label FROM media_event_annotations WHERE event_id=? ORDER BY dimension",
                (event_id,),
            ).fetchall()
        self.assertEqual([(row[0], row[1]) for row in rows], [("behavior", "eating"), ("identity", pet.pet_id)])
        self.assertEqual(self.store.cat_feedback_summary()["manual_annotation_events"],
                         {"behavior": 1, "identity": 1})
        self.assertEqual(self.store.telegram_update_offset(), 804)
        poller._process_update(
            805, self._callback(event_id=event_id, message_id=message_id, update_id=805, label="no"),
        )
        with self.store._connection_lock:
            current = self.store.connection.execute(
                "SELECT COUNT(*) FROM media_event_annotations WHERE event_id=?", (event_id,),
            ).fetchone()[0]
            history = self.store.connection.execute(
                "SELECT COUNT(*) FROM media_event_annotation_history WHERE event_id=?", (event_id,),
            ).fetchone()[0]
        self.assertEqual((current, history), (0, 2))

    def test_detector_video_uses_two_round_pet_wizard_with_opt_in(self):
        registry = PetRegistry(self.root / "pets")
        registry.register_pet("嗨啵", pet_id="highball")
        registry.register_pet("Saki", pet_id="saki")
        event_id, message_id, message = self._send_detector_alert()
        buttons = [b for row in message["reply_markup"]["inline_keyboard"] for b in row]
        self.assertEqual([b["text"] for b in buttons], ["是嗨啵", "是Saki", "兩隻都在", "不是貓咪"])
        self.store.record_telegram_receipt(event_id, hashlib.sha256(self.chat_id.encode()).hexdigest(),
                                           "video", message_id)
        poller = TelegramFeedbackPoller(self.telegram, self.store, [self.chat_id],
                                        pet_registry=registry, preferences=self.preferences)
        update = self._callback(event_id=event_id, message_id=message_id, update_id=901, label="yes")
        update["callback_query"]["data"] = buttons[2]["callback_data"]
        poller._process_update(901, update)
        self.assertIn("嗨啵的行為", self.telegram.sent_messages[-1]["text"])
        prompt = self.telegram.sent_messages[-1]
        update = self._callback(event_id=event_id, message_id=prompt["message_id"], update_id=902, label="yes")
        update["callback_query"]["data"] = prompt["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        poller._process_update(902, update)
        self.assertIn("Saki的行為", self.telegram.sent_messages[-1]["text"])
        self.preferences.set_feedback_enabled(self.chat_id, False)
        prompt = self.telegram.sent_messages[-1]
        update = self._callback(event_id=event_id, message_id=prompt["message_id"], update_id=903, label="yes")
        update["callback_query"]["data"] = prompt["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        poller._process_update(903, update)
        self.assertIn("尚未參與", self.telegram.answers[-1]["text"])
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM telegram_pet_review_history").fetchone()[0], 1)

    def test_annotation_requires_confirmed_cat_and_real_receipt(self):
        event_id, message_id, _ = self._send_detector_alert()
        self.store.record_telegram_receipt(
            event_id, hashlib.sha256(self.chat_id.encode()).hexdigest(), "video", message_id,
        )
        poller = TelegramFeedbackPoller(self.telegram, self.store, [self.chat_id], preferences=self.preferences)
        update = self._callback(event_id=event_id, message_id=999, update_id=811, label="yes")
        update["callback_query"]["data"] = f"fb:{event_id}:b:eating"
        poller._process_update(811, update)
        self.assertTrue(self.telegram.answers[-1]["show_alert"])
        with self.store._connection_lock:
            count = self.store.connection.execute("SELECT COUNT(*) FROM media_event_annotations").fetchone()[0]
        self.assertEqual(count, 0)

    def test_unauthorized_chat_and_group_are_rejected_and_offset_advances(self):
        event_id, message_id, _ = self._send_detector_alert()
        self.store.record_telegram_receipt(
            event_id, hashlib.sha256(self.chat_id.encode()).hexdigest(), "video", message_id,
        )
        poller = TelegramFeedbackPoller(self.telegram, self.store, [self.chat_id], preferences=self.preferences)
        poller._process_update(
            701, self._callback(event_id=event_id, message_id=message_id, update_id=701,
                                label="yes", chat_id="987654321"),
        )
        poller._process_update(
            702, self._callback(event_id=event_id, message_id=message_id, update_id=702,
                                label="yes", chat_id="123456789", user_id="123456789",
                                chat_type="group"),
        )
        self.assertEqual(self.store.cat_feedback_summary()["reviewed_events"], 0)
        self.assertEqual(self.store.telegram_update_offset(), 703)
        self.assertEqual(poller._offset, 703)
        self.assertTrue(all(answer["show_alert"] for answer in self.telegram.answers))

    def test_wrong_receipt_message_is_rejected_without_stalling_poll_offset(self):
        event_id, message_id, _ = self._send_detector_alert()
        self.store.record_telegram_receipt(
            event_id, hashlib.sha256(self.chat_id.encode()).hexdigest(), "video", message_id,
        )
        poller = TelegramFeedbackPoller(self.telegram, self.store, [self.chat_id], preferences=self.preferences)
        poller._process_update(
            801, self._callback(event_id=event_id, message_id=message_id + 1,
                                update_id=801, label="yes"),
        )
        self.assertEqual(self.store.cat_feedback_summary()["reviewed_events"], 0)
        self.assertEqual(self.store.telegram_update_offset(), 802)
        self.assertEqual(poller._offset, 802)
        self.assertTrue(self.telegram.answers[-1]["show_alert"])

    def test_native_video_track_binding_rejects_ambiguous_source_and_time(self):
        now = datetime.now(timezone.utc)
        media_id = str(uuid4())
        self.store.create_media_event(
            event_id=media_id, event_type="cat_detected", trigger_at=now,
            source_id="local", track_id="one", track_ids=("one", "two", "three"),
            pet_id="unknown", confidence=.8, requested_pre_seconds=5, requested_post_seconds=10)
        recipient = hashlib.sha256(self.chat_id.encode()).hexdigest()
        self.store.record_telegram_receipt(media_id, recipient, "video", 777)

        def event(track, source="local", delta=0):
            value = BehaviorEvent(None, track, BehaviorState.RESTING, MotionState.STATIONARY,
                                  (), now + timedelta(seconds=delta), now + timedelta(seconds=delta + 1),
                                  .8, None, "rule", "test", "test", source, None)
            self.store.save_behavior_event(value)
            return value

        first = event("one")
        second = event("one", delta=2)
        other_source = event("two", source="different")
        old = event("three", delta=-100)
        self.assertEqual(self.store.media_feedback_tracks(media_id), [])
        for value in (first, second, other_source, old):
            self.assertFalse(self.store.bind_media_track_receipt(value.event_id, recipient, 777))
        valid = event("three")
        self.assertEqual([row["event_id"] for row in self.store.media_feedback_tracks(media_id)], [valid.event_id])
        self.assertTrue(self.store.bind_media_track_receipt(valid.event_id, recipient, 777))
        self.assertTrue(self.store.bind_media_track_receipt(valid.event_id, recipient, 777))
        self.assertEqual(self.store.connection.execute(
            "SELECT COUNT(*) FROM behavior_event_telegram_receipts WHERE event_id=?", (valid.event_id,)).fetchone()[0], 1)
        self.store.connection.execute("UPDATE telegram_receipts SET received_at=datetime('now','-2 days')")
        self.store.connection.execute("DELETE FROM behavior_event_telegram_receipts")
        self.store.connection.commit()
        self.assertFalse(self.store.bind_media_track_receipt(valid.event_id, recipient, 777))

    def test_native_video_review_stages_identity_and_actions_until_confirmation(self):
        registry = PetRegistry(self.root / "pets")
        pet = registry.register_pet("測試貓", pet_id="test-pet")
        now = datetime.now(timezone.utc)
        event = BehaviorEvent(None, "native:1", BehaviorState.RESTING, MotionState.STATIONARY,
                              (), now, None, .8, None, "rule", "test", "test", "local", None)
        self.store.save_behavior_event(event)
        media_id = str(uuid4())
        self.store.create_media_event(event_id=media_id, event_type="cat_detected", trigger_at=now,
            source_id="local", track_id=event.track_id, pet_id="unknown", confidence=.8,
            requested_pre_seconds=5, requested_post_seconds=10)
        self.store.record_telegram_receipt(media_id, hashlib.sha256(self.chat_id.encode()).hexdigest(), "video", 777)
        poller = TelegramFeedbackPoller(self.telegram, self.store, [self.chat_id],
                                       pet_registry=registry, preferences=self.preferences)

        def click(data, update_id, message_id=777):
            if data.startswith(("who:", "tags:")):
                revision = self.store.connection.execute("SELECT callback_revision FROM telegram_track_tag_drafts WHERE review_id=?", (data.split(":")[1],)).fetchone()[0]
                data += f":{revision}"
            callback = self._callback(event_id=event.event_id, message_id=message_id, update_id=update_id, label="yes")
            callback["callback_query"]["data"] = data
            poller._process_update(update_id, callback)

        click(f"trk:{event.event_id}:open", 1500)
        prompt = self.telegram.sent_messages[-1]
        review = self.store.connection.execute("SELECT review_id FROM telegram_track_tag_drafts WHERE staged_identity=1").fetchone()[0]
        click(f"trk:{event.event_id}:open", 1500)
        self.assertEqual(len(self.telegram.sent_messages), 1)
        click(f"who:{review}:{poller._pet_token(pet.pet_id)}", 1501, prompt["message_id"])
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM behavior_event_annotations").fetchone()[0], 0)
        # 舊 be: 格式不能繞過新草稿的最後提交。
        click(f"be:{event.event_id}:i:{poller._pet_token(pet.pet_id)}", 1502, prompt["message_id"])
        self.assertTrue(self.telegram.answers[-1]["show_alert"])
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM behavior_event_annotations").fetchone()[0], 0)
        for index, operation in enumerate(("walking", "playing", "review", "done"), 1503):
            click(f"tags:{review}:{operation}", index, prompt["message_id"])
            self.assertFalse(self.telegram.answers[-1]["show_alert"])
        row = self.store.connection.execute("SELECT identity_snapshot,selected_json,state FROM telegram_track_tag_drafts").fetchone()
        self.assertEqual((row[0], json.loads(row[1]), row[2]), (pet.pet_id, ["walking", "playing"], "submitted"))
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM behavior_event_annotation_history").fetchone()[0], 1)
        self.assertEqual(len(self.telegram.sent_messages), 1)
        self.assertEqual(self.telegram.sent_videos, [])  # 回報不重傳原影片。

    def test_two_tracks_open_independent_feedback_and_keep_track_identity_separate(self):
        registry = PetRegistry(self.root / "pets")
        highball = registry.register_pet("嗨啵", pet_id="highball")
        saki = registry.register_pet("Saki", pet_id="saki")
        now = datetime.now(timezone.utc)

        def save_event(track_id, behavior, motion):
            event = BehaviorEvent(
                pet_id=None, track_id=track_id, behavior_state=behavior,
                motion_state=motion, event_tags=(), started_at=now,
                ended_at=now + timedelta(seconds=12), raw_confidence=0.86,
                smoothed_confidence=0.82, inference_method="rules",
                model_version="rules-v0.2", rule_profile_version="test",
                source_id="source-redacted", camera_preset=None,
            )
            self.store.save_behavior_event(event)
            return event

        first = save_event("session-one:track-1", BehaviorState.OTHER_ACTIVE, MotionState.WALKING)
        second = save_event("session-one:track-2", BehaviorState.EATING, MotionState.STATIONARY)
        media_id = str(uuid4())
        self.store.create_media_event(
            event_id=media_id, event_type="cat_detected", trigger_at=now,
            source_id="source-redacted", track_id=first.track_id,
            track_ids=(first.track_id, second.track_id), pet_id="unknown", confidence=.86,
            requested_pre_seconds=5, requested_post_seconds=10,
        )
        # Legacy finalized-track notifications retain their existing annotation route.
        for event in (first, second):
            self.store.record_behavior_event_telegram_receipt(
                event.event_id, hashlib.sha256(self.chat_id.encode()).hexdigest(), 777)
        service = EventClipService(Mock(), Mock(), output_directory=self.root / "events",
                                   store=self.store, cat_notification_preferences=self.preferences)
        try:
            markup = service._feedback_markup(self.chat_id, EventClipRequest(
                media_id, now, "cat_detected", "source-redacted"))
            self.assertEqual([row[0]["callback_data"] for row in markup["inline_keyboard"]],
                             [f"trk:{first.event_id}:open", f"trk:{second.event_id}:open"])
        finally:
            service.close()
        self.assertFalse(self.store.bind_media_track_receipt(first.event_id, hashlib.sha256(b"wrong").hexdigest(), 777))
        self.assertFalse(self.store.bind_media_track_receipt(first.event_id, hashlib.sha256(self.chat_id.encode()).hexdigest(), 778))
        poller = TelegramFeedbackPoller(
            self.telegram, self.store, [self.chat_id], pet_registry=registry,
            preferences=self.preferences,
        )

        def update(update_id, *, event_id, message_id, callback_data):
            item = self._callback(
                event_id=event_id, message_id=message_id, update_id=update_id, label="yes",
            )
            item["callback_query"]["data"] = callback_data
            return item

        # Previously delivered be: prompts remain supported, but new trk: entries use staged identity.
        poller._send_behavior_event_prompt(self.chat_id, first.event_id)
        first_prompt = self.telegram.sent_messages[-1]
        poller._send_behavior_event_prompt(self.chat_id, second.event_id)
        second_prompt = self.telegram.sent_messages[-1]
        first_prompt_callbacks = [
            button["callback_data"]
            for row in first_prompt["reply_markup"]["inline_keyboard"]
            for button in row
        ]
        second_prompt_callbacks = [
            button["callback_data"]
            for row in second_prompt["reply_markup"]["inline_keyboard"]
            for button in row
        ]
        self.assertNotIn(f"be:{first.event_id}:m:walking", first_prompt_callbacks)
        self.assertNotIn(f"be:{second.event_id}:m:stationary", second_prompt_callbacks)
        labels = [button["text"] for row in first_prompt["reply_markup"]["inline_keyboard"] for button in row]
        self.assertIn("其他貓", labels)
        self.assertIn("不是貓", labels)
        self.assertFalse(any("這條是貓咪" in label for label in labels))

        def choose(update_id, event, prompt, dimension, choice):
            data = f"be:{event.event_id}:{dimension}:{choice}"
            self.assertLessEqual(len(data.encode("utf-8")), 64)
            poller._process_update(update_id, update(
                update_id, event_id=event.event_id,
                message_id=int(prompt["message_id"]), callback_data=data,
            ))

        choose(1000, first, first_prompt, "b", "eating")
        self.assertIn("請先選", self.telegram.answers[-1]["text"])
        self.assertEqual(self.store.connection.execute(
            "SELECT COUNT(*) FROM behavior_event_annotations"
        ).fetchone()[0], 0)
        choose(1004, first, first_prompt, "i", TelegramFeedbackPoller._pet_token(highball.pet_id))
        action_prompt = self.telegram.sent_messages[-1]
        self.assertIn("第二步", action_prompt["text"])
        self.assertTrue(any(button["callback_data"].startswith("tags:") and button["callback_data"].endswith(":walking")
                            for row in action_prompt["reply_markup"]["inline_keyboard"] for button in row))
        actions = [button["callback_data"] for row in action_prompt["reply_markup"]["inline_keyboard"]
                   for button in row if button["callback_data"].startswith("tags:")]
        for number, tag in enumerate(("walking", "playing", "review", "done"), 1005):
            current_actions = actions if tag != "done" else [button["callback_data"] for row in self.telegram.edited_markups[-1]["reply_markup"]["inline_keyboard"] for button in row]
            poller._process_update(number, update(number, event_id=first.event_id,
                message_id=int(action_prompt["message_id"]),
                callback_data=next(data for data in current_actions if data.endswith(":" + tag))))
        draft = self.store.connection.execute(
            "SELECT event_id,selected_json,state FROM telegram_track_tag_drafts WHERE event_id=?", (first.event_id,),
        ).fetchone()
        self.assertEqual((draft["event_id"], json.loads(draft["selected_json"]), draft["state"]),
                         (first.event_id, ["walking", "playing"], "submitted"))
        choose(1009, first, first_prompt, "b", "other_active")
        choose(1010, first, first_prompt, "m", "walking")
        choose(1011, second, second_prompt, "c", "yes")
        choose(1012, second, second_prompt, "i", TelegramFeedbackPoller._pet_token(saki.pet_id))
        choose(1013, second, second_prompt, "b", "eating")
        choose(1014, second, second_prompt, "m", "stationary")

        rows = self.store.connection.execute(
            """SELECT a.event_id,e.track_id,a.dimension,a.label
               FROM behavior_event_annotations a
               JOIN behavior_events e ON e.event_id=a.event_id
               ORDER BY a.event_id,a.dimension""",
        ).fetchall()
        actual = {(row["track_id"], row["dimension"]): row["label"] for row in rows}
        self.assertEqual(actual, {
            ("session-one:track-1", "cat_presence"): "cat",
            ("session-one:track-1", "identity"): highball.pet_id,
            ("session-one:track-1", "behavior"): "other_active",
            ("session-one:track-1", "motion"): "walking",
            ("session-one:track-2", "cat_presence"): "cat",
            ("session-one:track-2", "identity"): saki.pet_id,
            ("session-one:track-2", "behavior"): "eating",
            ("session-one:track-2", "motion"): "stationary",
        })
        history_count = self.store.connection.execute(
            "SELECT COUNT(*) FROM behavior_event_annotation_history",
        ).fetchone()[0]
        self.assertEqual(history_count, 7)
        self.assertEqual(self.store.telegram_update_offset(), 1015)
        self.assertEqual(poller._offset, 1015)
        choose(1015, second, second_prompt, "c", "no")
        remaining = self.store.connection.execute(
            """SELECT e.track_id,a.dimension,a.label
               FROM behavior_event_annotations a
               JOIN behavior_events e ON e.event_id=a.event_id
               ORDER BY e.track_id,a.dimension""",
        ).fetchall()
        remaining_labels = {
            (row["track_id"], row["dimension"]): row["label"] for row in remaining
        }
        self.assertNotIn(("session-one:track-2", "identity"), remaining_labels)
        self.assertNotIn(("session-one:track-2", "behavior"), remaining_labels)
        self.assertNotIn(("session-one:track-2", "motion"), remaining_labels)
        self.assertEqual(remaining_labels[("session-one:track-2", "cat_presence")], "not_cat")
        self.assertEqual(
            self.store.connection.execute(
                "SELECT COUNT(*) FROM behavior_event_annotation_history",
            ).fetchone()[0],
            8,
        )
        self.assertEqual(self.store.telegram_update_offset(), 1016)
        self.assertEqual(poller._offset, 1016)
        consent_buttons = [button["callback_data"] for row in action_prompt["reply_markup"]["inline_keyboard"] for button in row]
        self.assertNotIn(f"be:{first.event_id}:l:yes", consent_buttons)
        automatic = self.store.connection.execute("SELECT learning_consent FROM behavior_event_annotations WHERE event_id=? AND dimension='identity'", (first.event_id,)).fetchone()[0]
        self.assertEqual(automatic, 1)
        # Older posted buttons remain reversible, but new prompts omit them.
        choose(1016, first, action_prompt, "l", "yes")
        consent = self.store.connection.execute("SELECT learning_consent FROM behavior_event_annotations WHERE event_id=? AND dimension='identity'", (first.event_id,)).fetchone()[0]
        self.assertEqual(consent, 1)
        choose(1017, first, action_prompt, "l", "no")
        consent = self.store.connection.execute("SELECT learning_consent FROM behavior_event_annotations WHERE event_id=? AND dimension='identity'", (first.event_id,)).fetchone()[0]
        self.assertEqual(consent, 0)

    def test_two_cat_digest_is_one_message_and_feedback_is_opt_in_per_recipient(self):
        registry = PetRegistry(self.root / "pets")
        highball = registry.register_pet("嗨啵", pet_id="highball")
        saki = registry.register_pet("Saki", pet_id="saki")
        now = datetime.now(timezone.utc)
        events = [
            BehaviorEvent(
                pet_id=highball.pet_id, track_id="private-track-one",
                behavior_state=BehaviorState.OTHER_ACTIVE, motion_state=MotionState.WALKING,
                event_tags=(), started_at=now, ended_at=now + timedelta(seconds=20),
                raw_confidence=0.83, smoothed_confidence=0.79, inference_method="rules",
                model_version="rules-v0.2", rule_profile_version="test",
                source_id="source-redacted", camera_preset=None,
            ),
            BehaviorEvent(
                pet_id=saki.pet_id, track_id="private-track-two",
                behavior_state=BehaviorState.EATING, motion_state=MotionState.STATIONARY,
                event_tags=(), started_at=now, ended_at=now + timedelta(seconds=45),
                raw_confidence=0.88, smoothed_confidence=0.81, inference_method="rules",
                model_version="rules-v0.2", rule_profile_version="test",
                source_id="source-redacted", camera_preset=None,
            ),
        ]
        for event in events:
            self.store.save_behavior_event(event)

        other_chat = "987654321"
        self.preferences.set_feedback_enabled(other_chat, False)
        dispatcher = NotificationDispatcher()
        self.addCleanup(dispatcher.shutdown, wait=True)
        pipeline = object.__new__(V02AnalysisPipeline)
        pipeline.notifier = self.telegram
        pipeline.chat_ids = (self.chat_id, other_chat)
        pipeline.notification_dispatcher = dispatcher
        pipeline.feedback_preferences = self.preferences
        pipeline.store = self.store
        pipeline.metrics = PerformanceMetrics()
        pipeline.logger = None
        from meowkeeper.core.identity.feedback_learning import FeedbackLearningPolicy
        pipeline._learning_policy = FeedbackLearningPolicy()

        tracks = []
        for index, (event, pet) in enumerate(zip(events, (highball, saki), strict=True), start=1):
            observation = TrackObservation(
                track_id=f"private-track-{index}", bbox=BoundingBox(10, 10, 80, 80),
                captured_at=now, confidence=0.9, source_id="source-redacted",
                frame_width=640, frame_height=360,
                crop_jpeg=(Path(__file__).parent / "assets" / "cat.jpg").read_bytes(),
            )
            tracks.append(V02TrackResult(
                observation=observation, pet_id=pet.pet_id, pet_name=pet.display_name,
                identity_confidence=0.73, behavior_state=event.behavior_state.value,
                behavior_confidence=event.smoothed_confidence or 0,
                events=(event,), identity_status="resolved", feedback_event=event,
            ))

        pipeline._dispatch_track_summary(tracks, [])
        dispatcher.shutdown(wait=True)

        by_chat = {item["chat_id"]: item for item in self.telegram.sent_messages}
        self.assertNotIn(self.chat_id, by_chat)
        opted_in_message = {**self.telegram.sent_photos[0], "text": self.telegram.sent_photos[0]["caption"]}
        self.assertIn("嗨啵", opted_in_message["text"])
        self.assertIn("行走", opted_in_message["text"])
        self.assertIn("Saki", opted_in_message["text"])
        self.assertIn("進食", opted_in_message["text"])
        self.assertNotIn("private-track", opted_in_message["text"])
        buttons = opted_in_message["reply_markup"]["inline_keyboard"]
        callbacks = [button["callback_data"] for row in buttons for button in row]
        self.assertEqual(callbacks, [
            f"trk:{events[0].event_id}:open", f"trk:{events[1].event_id}:open",
        ])
        self.assertIsNone(by_chat[other_chat]["reply_markup"])
        self.assertEqual(len(self.telegram.sent_photos), 1)
        self.assertEqual(self.telegram.sent_photos[0]["chat_id"], self.chat_id)
        self.assertIn("逐軌跡影像對照", self.telegram.sent_photos[0]["caption"])
        self.assertTrue(self.telegram.sent_photos[0]["payload"].startswith(b"\xff\xd8"))
        recipient_ref = hashlib.sha256(self.chat_id.encode()).hexdigest()
        for event in events:
            self.assertTrue(self.store.has_behavior_event_telegram_receipt(
                event.event_id, recipient_ref, opted_in_message["message_id"],
            ))

        # 超長摘要不得被 Telegram 的 caption 上限截斷，改用文字承載按鈕。
        poller = TelegramFeedbackPoller(
            self.telegram, self.store, [self.chat_id], pet_registry=registry,
            preferences=self.preferences,
        )
        photo_callback = self._callback(
            event_id=events[0].event_id, message_id=opted_in_message["message_id"],
            update_id=1201, label="yes",
        )
        photo_callback["callback_query"]["data"] = f"trk:{events[0].event_id}:open"
        poller._process_update(1201, photo_callback)
        self.assertFalse(self.telegram.answers[-1]["show_alert"])
        self.assertIsNotNone(self.telegram.sent_messages[-1]["reply_markup"])

        from dataclasses import replace
        long_tracks = [replace(track, pet_name="參考名稱" * 180) for track in tracks]
        pipeline.chat_ids = (self.chat_id,)
        pipeline.notification_dispatcher = NotificationDispatcher()
        pipeline._dispatch_track_summary(long_tracks, [])
        pipeline.notification_dispatcher.shutdown(wait=True)
        long_message = self.telegram.sent_messages[-1]
        self.assertIsNotNone(long_message["reply_markup"])
        self.assertIn("摘要較長", self.telegram.sent_photos[-1]["caption"])
        self.assertTrue(self.store.has_behavior_event_telegram_receipt(
            events[0].event_id, recipient_ref, long_message["message_id"],
        ))

        # 圖片傳送失敗仍提供文字與回饋，且回執屬於真正送達的文字。
        self.telegram.send_photo = Mock(side_effect=RuntimeError("安全測試錯誤"))
        pipeline.notification_dispatcher = NotificationDispatcher()
        pipeline._dispatch_track_summary(tracks, [])
        pipeline.notification_dispatcher.shutdown(wait=True)
        fallback = self.telegram.sent_messages[-1]
        self.assertIn("軌跡影像無法送出", fallback["text"])
        self.assertIsNotNone(fallback["reply_markup"])
        self.assertTrue(self.store.has_behavior_event_telegram_receipt(
            events[0].event_id, recipient_ref, fallback["message_id"],
        ))

        # 個人冷卻下，超長摘要只送一則文字，不先送第二張圖片。
        self.preferences.set_cooldown_minutes(self.chat_id, 15)
        before = len(self.telegram.sent_messages)
        self.telegram.send_photo.reset_mock()
        pipeline.notification_dispatcher = NotificationDispatcher()
        pipeline._dispatch_track_summary(long_tracks, [])
        pipeline.notification_dispatcher.shutdown(wait=True)
        self.assertEqual(len(self.telegram.sent_messages), before + 1)
        self.telegram.send_photo.assert_not_called()
        pipeline.notification_dispatcher = NotificationDispatcher()
        pipeline._dispatch_track_summary(tracks, [])
        pipeline.notification_dispatcher.shutdown(wait=True)
        self.assertEqual(len(self.telegram.sent_messages), before + 1)
        self.assertFalse(self.preferences.claim_delivery(self.chat_id, "cat_detected"))

    def test_motion_dimension_migration_preserves_prior_feedback_and_is_repeatable(self):
        now = datetime.now(timezone.utc)
        event = BehaviorEvent(
            pet_id=None, track_id="migration-track", behavior_state=BehaviorState.OTHER_ACTIVE,
            motion_state=MotionState.WALKING, event_tags=(), started_at=now, ended_at=now,
            raw_confidence=0.8, smoothed_confidence=0.8, inference_method="rules",
            model_version="rules-v0.2", rule_profile_version="test",
            source_id="source-redacted", camera_preset=None,
        )
        self.store.save_behavior_event(event)
        recipient_ref = hashlib.sha256(self.chat_id.encode()).hexdigest()
        reviewer_ref = hashlib.sha256(b"reviewer").hexdigest()
        self.store.record_behavior_event_telegram_receipt(event.event_id, recipient_ref, 999)
        for update_id, dimension, label in (
            (1100, "cat_presence", "cat"),
            (1101, "behavior", "other_active"),
        ):
            self.store.record_behavior_event_annotation(
                event_id=event.event_id, recipient_ref=recipient_ref,
                reviewer_ref=reviewer_ref, message_id=999, dimension=dimension,
                label=label, update_id=update_id,
                callback_ref=hashlib.sha256(f"callback-{update_id}".encode()).hexdigest(),
            )

        connection = self.store.connection
        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        for table, create_sql in (
            ("behavior_event_annotations", """
                CREATE TABLE behavior_event_annotations_legacy (
                    event_id TEXT NOT NULL REFERENCES behavior_events(event_id) ON DELETE CASCADE,
                    reviewer_ref TEXT NOT NULL,
                    dimension TEXT NOT NULL CHECK (dimension IN ('cat_presence','identity','behavior')),
                    label TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (event_id, reviewer_ref, dimension)
                )
            """),
            ("behavior_event_annotation_history", """
                CREATE TABLE behavior_event_annotation_history_legacy (
                    update_id INTEGER PRIMARY KEY,
                    callback_ref TEXT NOT NULL UNIQUE,
                    event_id TEXT NOT NULL REFERENCES behavior_events(event_id) ON DELETE CASCADE,
                    reviewer_ref TEXT NOT NULL,
                    dimension TEXT NOT NULL CHECK (dimension IN ('cat_presence','identity','behavior')),
                    label TEXT NOT NULL,
                    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """),
        ):
            legacy = f"{table}_legacy"
            connection.execute(create_sql)
            legacy_columns = ",".join(row[1] for row in connection.execute(f"PRAGMA table_info({legacy})"))
            connection.execute(f"INSERT INTO {legacy} SELECT {legacy_columns} FROM {table}")
            connection.execute(f"DROP TABLE {table}")
            connection.execute(f"ALTER TABLE {legacy} RENAME TO {table}")
        connection.execute("UPDATE schema_meta SET value='10' WHERE key='schema_version'")
        connection.execute("PRAGMA user_version = 10")
        connection.commit()
        connection.execute("PRAGMA foreign_keys = ON")

        self.store.initialize()
        self.store.initialize()
        self.assertEqual(self.store.schema_version, SCHEMA_VERSION)
        rows = self.store.connection.execute(
            "SELECT dimension,label FROM behavior_event_annotations WHERE event_id=? ORDER BY dimension",
            (event.event_id,),
        ).fetchall()
        self.assertEqual({(row["dimension"], row["label"]) for row in rows}, {
            ("behavior", "other_active"), ("cat_presence", "cat"),
        })
        self.store.record_behavior_event_annotation(
            event_id=event.event_id, recipient_ref=recipient_ref,
            reviewer_ref=reviewer_ref, message_id=999, dimension="motion",
            label="walking", update_id=1102,
            callback_ref=hashlib.sha256(b"callback-1102").hexdigest(),
        )
        self.assertEqual(
            self.store.connection.execute(
                "PRAGMA foreign_key_check",
            ).fetchall(),
            [],
        )
        self.assertEqual(
            self.store.connection.execute(
                "SELECT COUNT(*) FROM behavior_event_annotation_history WHERE event_id=?",
                (event.event_id,),
            ).fetchone()[0],
            3,
        )

    def test_track_feedback_button_requires_recipient_receipt_and_opt_in(self):
        now = datetime.now(timezone.utc)
        event = BehaviorEvent(
            pet_id=None, track_id="session:track", behavior_state=BehaviorState.OTHER_ACTIVE,
            motion_state=MotionState.WALKING, event_tags=(), started_at=now,
            ended_at=now, raw_confidence=0.8, smoothed_confidence=0.8,
            inference_method="rules", model_version="rules-v0.2",
            rule_profile_version="test", source_id="source-redacted", camera_preset=None,
        )
        self.store.save_behavior_event(event)
        self.store.record_behavior_event_telegram_receipt(
            event.event_id, hashlib.sha256(self.chat_id.encode()).hexdigest(), 888,
        )
        poller = TelegramFeedbackPoller(self.telegram, self.store, [self.chat_id], preferences=self.preferences)
        forged = self._callback(
            event_id=event.event_id, message_id=889, update_id=1010, label="yes",
        )
        forged["callback_query"]["data"] = f"trk:{event.event_id}:open"
        poller._process_update(1010, forged)
        self.assertEqual(self.telegram.sent_messages, [])
        self.assertTrue(self.telegram.answers[-1]["show_alert"])

        self.preferences.set_feedback_enabled(self.chat_id, False)
        valid_receipt = self._callback(
            event_id=event.event_id, message_id=888, update_id=1011, label="yes",
        )
        valid_receipt["callback_query"]["data"] = f"trk:{event.event_id}:open"
        poller._process_update(1011, valid_receipt)
        self.assertEqual(self.telegram.sent_messages, [])
        self.assertIn("尚未參與回饋", self.telegram.answers[-1]["text"])

    def test_v43_semantic_candidate_native_video_and_track_feedback_close_loop(self):
        """One isolated path covers candidate caption -> sent video receipt -> per-track SQLite feedback."""
        registry = PetRegistry(self.root / "pets")
        pet = registry.register_pet("嗨啵", pet_id="highball")
        now = datetime.now(timezone.utc)
        track_id = "session-v43:track-1"
        behavior_event = BehaviorEvent(
            pet_id=None, track_id=track_id, behavior_state=BehaviorState.OTHER_ACTIVE,
            motion_state=MotionState.WALKING, event_tags=(), started_at=now - timedelta(seconds=1),
            ended_at=now + timedelta(seconds=4), raw_confidence=0.86,
            smoothed_confidence=0.82, inference_method="rules", model_version="test-model",
            rule_profile_version="test", source_id="local-test", camera_preset=None,
        )
        self.store.save_behavior_event(behavior_event)
        self.store.create_media_event(
            event_id=behavior_event.event_id, event_type="cat_detected", trigger_at=now,
            source_id="local-test", track_id=None, track_ids=(track_id,), pet_id="unknown",
            confidence=0.86, requested_pre_seconds=2, requested_post_seconds=5,
            trigger_origin="detector",
        )
        candidate = SemanticResult("completed", ((track_id, ("walking", "playing")),))
        self.store.record_event_semantics(behavior_event.event_id, candidate)
        future = SemanticFuture()
        future.set_result(candidate)

        clip = self.root / "v43-event.mp4"
        clip.write_bytes(b"isolated-test-mp4")
        request = EventClipRequest(
            behavior_event.event_id, now, "cat_detected", "local-test",
            trigger_origin="detector", track_ids=(track_id,), confidence=0.86,
        )
        attempts, error = self.service._send_with_retries(
            request, clip, "🐈 貓咪剛剛出現在畫面。\n事件影片",
            semantics=future, clip_result=SimpleNamespace(actual_duration_seconds=6),
        )
        self.assertEqual((attempts, error), (1, None))
        video = self.telegram.sent_videos[-1]
        self.assertIn("可能在行走、玩耍", video["caption"])
        self.assertIn("AI 候選", video["caption"])
        self.assertEqual(video["width"], 320)
        track_button = video["reply_markup"]["inline_keyboard"][0][0]
        self.assertEqual(track_button["callback_data"], f"trk:{behavior_event.event_id}:open")
        message_id = int(video["message_id"])

        recipient_ref = hashlib.sha256(self.chat_id.encode()).hexdigest()
        receipt = self.store.connection.execute(
            "SELECT media_kind,message_id FROM telegram_receipts WHERE event_id=? AND recipient_ref=?",
            (behavior_event.event_id, recipient_ref),
        ).fetchone()
        self.assertEqual(tuple(receipt), ("video", message_id))
        track_ref = self.store.connection.execute(
            "SELECT behavior_event_id FROM telegram_media_track_refs WHERE media_event_id=? AND recipient_ref=? AND message_id=?",
            (behavior_event.event_id, recipient_ref, message_id),
        ).fetchone()
        self.assertEqual(track_ref[0], behavior_event.event_id)

        poller = TelegramFeedbackPoller(
            self.telegram, self.store, [self.chat_id], pet_registry=registry,
            preferences=self.preferences,
        )

        def click(update_id: int, message_id: int, callback_data: str) -> None:
            item = self._callback(
                event_id=behavior_event.event_id, message_id=message_id,
                update_id=update_id, label="yes",
            )
            item["callback_query"]["data"] = callback_data
            poller._process_update(update_id, item)

        click(2201, message_id, track_button["callback_data"])
        prompt = self.telegram.sent_messages[-1]
        review_id = self.store.connection.execute(
            "SELECT review_id FROM telegram_track_tag_drafts WHERE event_id=? AND staged_identity=1",
            (behavior_event.event_id,),
        ).fetchone()[0]

        def revision() -> int:
            return int(self.store.connection.execute(
                "SELECT callback_revision FROM telegram_track_tag_drafts WHERE review_id=?", (review_id,),
            ).fetchone()[0])

        click(2202, int(prompt["message_id"]),
              f"who:{review_id}:{poller._pet_token(pet.pet_id)}:{revision()}")
        click(2203, int(prompt["message_id"]), f"tags:{review_id}:walking:{revision()}")
        click(2204, int(prompt["message_id"]), f"tags:{review_id}:playing:{revision()}")
        click(2205, int(prompt["message_id"]), f"tags:{review_id}:review:{revision()}")
        click(2206, int(prompt["message_id"]), f"tags:{review_id}:done:{revision()}")

        draft = self.store.connection.execute(
            "SELECT selected_json,state,identity_snapshot FROM telegram_track_tag_drafts WHERE review_id=?",
            (review_id,),
        ).fetchone()
        self.assertEqual((json.loads(draft["selected_json"]), draft["state"], draft["identity_snapshot"]),
                         (["walking", "playing"], "submitted", pet.pet_id))
        annotations = {
            row["dimension"]: row["label"] for row in self.store.connection.execute(
                "SELECT dimension,label FROM behavior_event_annotations WHERE event_id=?",
                (behavior_event.event_id,),
            ).fetchall()
        }
        self.assertEqual(annotations, {"cat_presence": "cat", "identity": pet.pet_id})
        self.assertEqual(self.store.connection.execute(
            "SELECT COUNT(*) FROM behavior_event_annotation_history WHERE event_id=?",
            (behavior_event.event_id,),
        ).fetchone()[0], 1)
        self.assertEqual(self.store.connection.execute(
            "SELECT COUNT(*) FROM telegram_track_tag_history WHERE review_id=?", (review_id,),
        ).fetchone()[0], 5)
        self.assertEqual(len(self.telegram.sent_videos), 1)  # feedback callbacks never resend the video
        self.assertEqual(self.store.event_semantics(behavior_event.event_id), candidate.payload())

    def test_single_long_poll_worker_receives_and_commits_realistic_callback_update(self):
        event_id, message_id, _ = self._send_detector_alert()
        self.store.record_telegram_receipt(
            event_id, hashlib.sha256(self.chat_id.encode()).hexdigest(), "video", message_id,
        )
        callback = self._callback(event_id=event_id, message_id=message_id, update_id=901, label="yes")
        notifier = _PollingTelegram([callback])
        poller = TelegramFeedbackPoller(notifier, self.store, [self.chat_id], preferences=self.preferences)
        poller.start()
        try:
            deadline = time.monotonic() + 2.0
            while not notifier.answers and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            poller.close()
        self.assertTrue(notifier.answers)
        self.assertGreaterEqual(notifier.get_updates_calls, 1)
        self.assertEqual(self.store.telegram_update_offset(), 902)
        self.assertEqual(self.store.cat_feedback_summary()["confirmed_cat"], 1)

    def test_existing_webhook_fails_closed_without_deleting_or_polling(self):
        log_messages: list[str] = []
        notifier = _PollingTelegram([], webhook_url="https://secret.example/hook")
        poller = TelegramFeedbackPoller(
            notifier, self.store, [self.chat_id], logger=log_messages.append,
        )
        poller.start()
        poller._thread.join(timeout=2.0)
        self.assertFalse(poller.running)
        self.assertEqual(notifier.get_updates_calls, 0)
        self.assertEqual(len(log_messages), 1)
        self.assertNotIn("secret.example", log_messages[0])

    def test_jpeg_fallback_alert_also_accepts_receipt_bound_feedback(self):
        event_id = str(uuid4())
        triggered = datetime.now(timezone.utc)
        self.store.create_media_event(
            event_id=event_id, event_type="cat_detected", trigger_at=triggered,
            source_id="local-test", track_id=None, pet_id="unknown", confidence=0.61,
            requested_pre_seconds=5, requested_post_seconds=10, trigger_origin="detector",
        )
        image = self.root / f"{event_id}.jpg"
        image.write_bytes(b"jpeg-test")
        request = EventClipRequest(
            event_id, triggered, "cat_detected", "local-test", confidence=0.61,
            screenshot_bytes=b"jpeg-test",
        )
        self.service._queue_snapshot_telegram(request, image, "local clip unavailable")
        self.assertTrue(self.service.wait_for_idle(timeout_seconds=3))
        message = self.telegram.sent_photos[-1]
        self.assertEqual([item["text"] for item in message["reply_markup"]["inline_keyboard"][0]],
                         ["✅ 是貓咪", "❌ 不是貓咪"])
        poller = TelegramFeedbackPoller(self.telegram, self.store, [self.chat_id], preferences=self.preferences)
        poller._process_update(
            550, self._callback(event_id=event_id, message_id=int(message["message_id"]),
                                update_id=550, label="yes"),
        )
        summary = self.store.cat_feedback_summary()
        self.assertEqual((summary["confirmed_cat"], summary["confirmed_not_cat"]), (1, 0))

    def test_disabled_or_simulated_alert_does_not_offer_training_feedback(self):
        event_id = str(uuid4())
        triggered = datetime.now(timezone.utc)
        self.store.create_media_event(
            event_id=event_id, event_type="cat_detected", trigger_at=triggered,
            source_id="local-test", track_id=None, pet_id="unknown", confidence=0.8,
            requested_pre_seconds=0, requested_post_seconds=0, trigger_origin="simulated",
        )
        clip = self.root / "simulated.mp4"
        clip.write_bytes(b"test")
        request = EventClipRequest(
            event_id, triggered, "cat_detected", "local-test", confidence=0.8,
            trigger_origin="simulated",
        )
        result = ClipAssemblyResult(clip, "completed", 1.0, True, size_bytes=4)
        self.service._queue_telegram(request, result, ())
        self.assertTrue(self.service.wait_for_idle(timeout_seconds=3))
        self.assertIsNone(self.telegram.sent_videos[-1]["reply_markup"])

        disabled = EventClipService(Mock(), Mock(), output_directory=self.root / "disabled",
                                    store=self.store, notifier=self.telegram,
                                    chat_ids=[self.chat_id], telegram_enabled=False)
        try:
            sent_before = len(self.telegram.sent_videos)
            disabled._queue_telegram(request, result, ())
            self.assertEqual(len(self.telegram.sent_videos), sent_before)
        finally:
            disabled.close()

    def test_wilson_interval_exposes_uncertainty_for_small_samples(self):
        interval = SQLiteStore._wilson_interval(1, 1)
        self.assertIsNotNone(interval)
        self.assertLess(interval["lower"], 1.0)
        self.assertEqual(interval["upper"], 1.0)
        self.assertIsNone(SQLiteStore._wilson_interval(0, 0))


if __name__ == "__main__":
    unittest.main()
