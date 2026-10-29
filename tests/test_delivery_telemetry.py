"""可控時鐘驗證通知時序；不以 FakeTelegram 當實機驗收。"""

import copy
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from meowkeeper.core.delivery_telemetry import validate_delivery_trace
from meowkeeper.core.event_semantics import SemanticResult, VisionFailure
from meowkeeper.core.media.event_clip import EventClipRequest, EventClipService
from meowkeeper.storage.database import SCHEMA_VERSION, SQLiteStore


class DeliveryTelemetryTests(unittest.TestCase):
    def run_delivery(self, grace=5, *, result=None, failure=None, never=False, retry=False,
                     transcode=False, late=False, resolve_after=0.0, upload_seconds=0.2):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        path = root / "clip.mp4"
        path.write_bytes(b"synthetic")
        store = SQLiteStore(root / "events.db")
        self.addCleanup(store.close)
        tick = [0.0]
        base = datetime(2026, 10, 15, tzinfo=timezone.utc)
        utc = lambda: base + timedelta(seconds=tick[0])
        store.create_media_event(event_id="event", event_type="cat_detected", trigger_at=base,
                                 source_id="test", track_id=None, pet_id=None, confidence=.8,
                                 requested_pre_seconds=5, requested_post_seconds=10)
        future = Mock()

        def resolve(timeout):
            self.assertGreaterEqual(tick[0], 7 if transcode else 3)
            if never:
                tick[0] += timeout
                raise TimeoutError()
            if late:
                tick[0] += timeout+1  # Future不守期限／程序遲醒，也不能採用晚到候選。
            elif resolve_after > timeout:
                tick[0] += timeout
                raise TimeoutError()
            else:
                tick[0] += resolve_after
            if failure is not None:
                raise failure
            return result
        future.result.side_effect = resolve
        future.done.return_value = not (late or never or resolve_after > 0)
        notifier = Mock()
        sent = []

        def upload(chat_id, *_args, **_kwargs):
            tick[0] += upload_seconds
            sent.append(chat_id)
            if retry and chat_id == "two" and sent.count("two") == 1:
                raise OSError("must-not-save-private-response")
            return SimpleNamespace(message_id=len(sent))
        notifier.send_video.side_effect = upload
        assembler = Mock()
        compatible = SimpleNamespace(valid=True, telegram_compatible=True, width=320, height=240, duration_seconds=15)

        def validate(_path):
            tick[0] += 3
            return SimpleNamespace(valid=True, telegram_compatible=False) if transcode else compatible

        def convert(_source, target):
            tick[0] += 4
            target.write_bytes(b"converted")
            return SimpleNamespace(path=target, validation=compatible)
        assembler.transcode_for_telegram.side_effect = convert
        service = EventClipService(Mock(), assembler, output_directory=root, store=store,
                                   notifier=notifier, chat_ids=("one", "two") if retry else ("one",),
                                   semantic_wait_seconds=grace, clock=utc, monotonic=lambda: tick[0],
                                   retry_sleep=lambda seconds: tick.__setitem__(0, tick[0]+seconds))
        service._caption = Mock(side_effect=AssertionError("cannot-read-pet-or-sqlite-after-grace"))
        try:
            with patch("meowkeeper.core.media.event_clip.validate_media", side_effect=validate):
                outcome = service._send_with_retries(EventClipRequest("event", base, "cat_detected", "test"),
                    path, "local", semantics=future, clip_result=SimpleNamespace(actual_duration_seconds=15),
                    prepared_since=0)
        finally:
            service.close()
        self.assertIsNone(outcome[1])
        history = store.event_delivery_history("event")
        self.assertEqual(len(history), 1)
        self.assertEqual(future.result.call_count, 1)
        return history[0], store, notifier, sent

    def test_late_candidate_is_discarded_without_post_grace_database_reads(self):
        trace, _, notifier, _ = self.run_delivery(5, result=SemanticResult("completed", (("scene", ("walking",)),)), late=True)
        self.assertEqual(trace["extra_wait_ms"], 6000)  # 不把實測違限值截小。
        self.assertEqual((trace["wait_status"], trace["caption_source"]), ("timeout", "local"))
        self.assertEqual(notifier.send_video.call_args.kwargs["caption"], "local")

    def test_zero_five_ten_grace_only_begins_after_video_is_sendable(self):
        for grace in (0, 5, 10):
            with self.subTest(grace=grace):
                trace, _, _, _ = self.run_delivery(grace, never=True, transcode=True)
                self.assertEqual(trace["validation_transcode_ms"], 7000)
                self.assertEqual(trace["video_prepare_ms"], 7000)
                self.assertEqual(trace["extra_wait_ms"], grace*1000)
                self.assertEqual(trace["wait_budget_ms"], grace*1000)
                self.assertEqual(trace["wait_status"], "timeout")
                self.assertEqual(trace["caption_source"], "local")
                self.assertAlmostEqual(trace["uploads"][0]["elapsed_ms"], 200)
                self.assertTrue(trace["uploads"][0]["accepted"])
                self.assertGreaterEqual(trace["wait_started_at"], trace["media_sendable_at"])

    def test_ready_candidate_and_errors_do_not_delay_or_drop_video(self):
        cases = [(SemanticResult("completed", (("track", ("walking",)),)), None, "completed", "ai_candidate"),
                 (SemanticResult("completed"), None, "candidate_empty", "local"),
                 (None, OSError("private-provider-response"), "failed", "local"),
                 ({"raw": "private-provider-response"}, None, "bad_response", "local")]
        for result, failure, status, source in cases:
            with self.subTest(status=status):
                trace, _, notifier, _ = self.run_delivery(result=result, failure=failure)
                self.assertEqual((trace["wait_status"], trace["caption_source"], trace["extra_wait_ms"]), (status, source, 0))
                self.assertEqual(notifier.send_video.call_count, 1)
                self.assertNotIn("private-provider-response", json.dumps(trace))

    def test_candidate_finishing_three_seconds_after_media_ready_is_used(self):
        result = SemanticResult("completed", (("track", ("walking",)),))
        trace, _, notifier, _ = self.run_delivery(5, result=result, resolve_after=3)
        self.assertEqual(trace["wait_started_at"], trace["media_sendable_at"])
        self.assertEqual(trace["extra_wait_ms"], 3000)
        self.assertEqual((trace["wait_status"], trace["caption_source"]), ("completed", "ai_candidate"))
        self.assertRegex(notifier.send_video.call_args.kwargs["caption"], r"行走|走動")

    def test_provider_http_429_falls_back_immediately_without_retrying_ai(self):
        failure = VisionFailure("rate_limited", http_status=429, retry_after_seconds=30)
        trace, _, notifier, _ = self.run_delivery(failure=failure)
        self.assertEqual((trace["wait_status"], trace["extra_wait_ms"]), ("failed", 0))
        self.assertEqual(notifier.send_video.call_count, 1)
        self.assertEqual(notifier.send_video.call_args.kwargs["caption"], "local")

    def test_slow_telegram_upload_is_not_counted_as_ai_grace_wait(self):
        trace, _, notifier, _ = self.run_delivery(upload_seconds=12)
        self.assertEqual(trace["extra_wait_ms"], 0)
        self.assertEqual(trace["uploads"][0]["elapsed_ms"], 12000)
        self.assertTrue(trace["uploads"][0]["accepted"])
        self.assertEqual(notifier.send_video.call_count, 1)

    def test_retries_record_separate_uploads_without_rewaiting_or_resending_first_chat(self):
        trace, _, _, sent = self.run_delivery(5, never=True, retry=True)
        self.assertEqual(sent, ["one", "two", "two"])
        self.assertEqual([item["accepted"] for item in trace["uploads"]], [True, False, True])
        self.assertEqual([item["attempt"] for item in trace["uploads"]], [1, 1, 2])
        self.assertEqual(trace["extra_wait_ms"], 5000)
        self.assertNotIn("must-not-save-private-response", json.dumps(trace))
        self.assertTrue(all(len(item["recipient_ref"]) == 64 for item in trace["uploads"]))

    def test_whitelist_idempotency_and_append_only_history(self):
        trace, store, _, _ = self.run_delivery(never=True)
        for key, value in (("cookie", "secret"), ("wait_budget_ms", 10001), ("extra_wait_ms", float("nan"))):
            invalid = copy.deepcopy(trace)
            invalid[key] = value
            with self.assertRaises(ValueError):
                validate_delivery_trace(invalid)
        store.record_delivery_trace("event", trace)
        self.assertEqual(len(store.event_delivery_history("event")), 1)
        with self.assertRaises(sqlite3.IntegrityError):
            store.connection.execute("UPDATE event_delivery_traces SET trace_json='{}'")
        store.connection.rollback()
        self.assertEqual(store.event_delivery_history("event")[0], trace)

    def test_schema21_migration_preserves_event_and_is_repeatable(self):
        _, store, _, _ = self.run_delivery(never=True)
        store.connection.execute("DROP TABLE event_delivery_traces")
        store.connection.execute("UPDATE schema_meta SET value='21' WHERE key='schema_version'")
        store.connection.execute("PRAGMA user_version=21")
        store.connection.commit()
        store.initialize()
        store.initialize()
        self.assertEqual(store.schema_version, SCHEMA_VERSION)
        self.assertIsNotNone(store.get_media_event("event"))
        self.assertEqual(store.event_delivery_history("event"), [])
