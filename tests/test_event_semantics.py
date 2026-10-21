"""V4.3: API 候選不可延遲影音；有界等待只從影片準備完成開始。"""

import tempfile
import threading
import time
import unittest
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from meowkeeper.core.event_semantics import (
    EventSemanticService,
    SemanticRequest,
    SemanticResult,
    normalize_result,
)
from meowkeeper.core.media.event_clip import EventClipRequest, EventClipService
from meowkeeper.core.media.ring_buffer import DiskSegmentRingBuffer
from meowkeeper.storage.database import SCHEMA_VERSION, SQLiteStore


class EventSemanticTests(unittest.TestCase):
    def test_unknown_tags_and_invented_tracks_are_rejected_without_persisting_raw_text(self):
        request = SemanticRequest("event", b"jpeg", ("track-1",))
        for raw in [
            {"tracks": [{"track_id": "invented", "tags": ["walking"]}]},
            {"tracks": [{"track_id": "track-1", "tags": ["secret=https://example.invalid"]}]},
        ]:
            with self.assertRaises(ValueError):
                normalize_result(raw, request)
        result = normalize_result({"tracks": [{"track_id": "track-1", "tags": ["walking", "playing"]}],
                                   "summary": "private response must be discarded"}, request)
        self.assertNotIn("private", str(result.payload()))

    def test_single_worker_backpressure_and_nonblocking_submit(self):
        gate = threading.Event()
        entered = threading.Event()

        class Provider:
            def analyze(self, request, *, timeout_seconds):
                entered.set()
                gate.wait(1)
                return {"tracks": [{"track_id": "scene", "tags": ["resting"]}]}

        service = EventSemanticService(Provider(), max_pending=1)
        try:
            first = service.submit(SemanticRequest("first", b"jpeg"))
            self.assertTrue(entered.wait(1))
            second = service.submit(SemanticRequest("second", b"jpeg"))
            started = time.monotonic()
            third = service.submit(SemanticRequest("third", b"jpeg"))
            self.assertEqual(third.result().status, "queue_full")
            self.assertLess(time.monotonic() - started, .1)
            gate.set()
            self.assertEqual(first.result(1).status, "completed")
            self.assertEqual(second.result(1).status, "completed")
        finally:
            gate.set()
            service.close()

    def test_event_submission_runs_vision_and_clip_assembly_concurrently(self):
        """The event path must not wait for optional vision before assembling its native clip."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = SQLiteStore(root / "events.sqlite3")
            provider_entered = threading.Event()
            release_provider = threading.Event()
            provider_finished = threading.Event()
            assembly_entered = threading.Event()
            release_assembly = threading.Event()

            class Provider:
                def analyze(self, request, *, timeout_seconds):
                    self.request = request
                    provider_entered.set()
                    if not release_provider.wait(3):
                        raise TimeoutError("isolated provider gate timed out")
                    provider_finished.set()
                    return {"tracks": [{"track_id": "track-1", "tags": ["walking"]}]}

            provider = Provider()
            semantic_service = EventSemanticService(provider, result_sink=store.record_event_semantics)

            class BlockingAssembler:
                def assemble(self, _records, output_path):
                    assembly_entered.set()
                    if not release_assembly.wait(3):
                        raise TimeoutError("isolated clip assembly gate timed out")
                    output = Path(output_path)
                    output.write_bytes(b"isolated-native-video")
                    return SimpleNamespace(
                        path=output, actual_duration_seconds=2.0,
                        size_bytes=output.stat().st_size, sha256="a" * 64,
                    )

            ring = DiskSegmentRingBuffer(root / "ring")
            clip_service = EventClipService(
                ring, BlockingAssembler(), output_directory=root / "events-out",
                store=store, semantic_service=semantic_service,
                telegram_enabled=False, segment_finalize_wait_seconds=0,
            )
            event_id = "v43-parallel-event"
            request = EventClipRequest(
                event_id, datetime.now(timezone.utc), "cat_detected", "isolated-source",
                track_ids=("track-1",), screenshot_bytes=b"representative-jpeg",
                pre_seconds=0, post_seconds=0,
            )
            try:
                started = time.monotonic()
                self.assertTrue(clip_service.submit_event(request))
                self.assertLess(time.monotonic() - started, 0.5)
                self.assertTrue(provider_entered.wait(2))
                self.assertTrue(assembly_entered.wait(2))
                self.assertFalse(provider_finished.is_set())
                self.assertEqual(provider.request.event_id, event_id)
                self.assertEqual(provider.request.track_ids, ("track-1",))

                # Let clip preparation finish while the optional provider remains blocked.
                release_assembly.set()
                self.assertTrue(clip_service.wait_for_idle(timeout_seconds=2))
                self.assertEqual(store.get_media_event(event_id)["clip_status"], "completed")
                self.assertFalse(provider_finished.is_set())

                release_provider.set()
                deadline = time.monotonic() + 2
                while (store.event_semantics(event_id).get("status") == "not_requested"
                       and time.monotonic() < deadline):
                    time.sleep(0.01)
                saved_candidate = store.event_semantics(event_id)
                self.assertEqual(
                    saved_candidate["tracks"],
                    [{"track_id": "track-1", "tags": ["walking"]}],
                    f"{saved_candidate}; failed_saves={semantic_service.failed_saves}; "
                    f"provider_finished={provider_finished.is_set()}; worker_alive={semantic_service._worker.is_alive()}; "
                    f"history={store.event_semantic_history(event_id)}",
                )
            finally:
                release_assembly.set()
                release_provider.set()
                clip_service.close()
                semantic_service.close()
                store.close()

    def test_late_result_is_saved_separately_from_human_reviews_and_migration_repeats(self):
        store = SQLiteStore(":memory:")
        try:
            store.create_media_event(event_id="event", event_type="cat_detected",
                                     trigger_at=datetime.now(timezone.utc), source_id="test",
                                     track_id=None, pet_id=None, confidence=.8,
                                     requested_pre_seconds=5, requested_post_seconds=10)
            result = SemanticResult("completed", (("track-1", ("walking", "playing")),))
            store.record_event_semantics("event", result)
            store.connection.execute("UPDATE schema_meta SET value='16' WHERE key='schema_version'")
            store.connection.commit()
            store.initialize()
            store.initialize()
            self.assertEqual(store.schema_version, SCHEMA_VERSION)
            self.assertEqual(store.event_semantics("event"), result.payload())
            self.assertEqual(store.get_media_event("event")["pet_id"], "unknown")
        finally:
            store.close()

    def test_wait_happens_after_conversion_and_send_video_keeps_caption(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "event.mp4"
            path.write_bytes(b"media")
            ready = threading.Event()
            waits = []

            class GuardedFuture(Future):
                def result(self, timeout=None):
                    self.assert_ready = ready.is_set()
                    waits.append(timeout)
                    return super().result(timeout)

            future = GuardedFuture()
            future.set_result(SemanticResult("completed", (("scene", ("walking", "playing")),)))
            compatible = SimpleNamespace(valid=True, telegram_compatible=True, width=1920, height=1080,
                                         duration_seconds=15)
            assembler = Mock()

            def convert(source, target):
                target.write_bytes(b"converted")
                ready.set()
                return SimpleNamespace(path=target, validation=compatible)

            assembler.transcode_for_telegram.side_effect = convert
            notifier = Mock()
            notifier.send_video.return_value = SimpleNamespace(message_id=42)
            service = EventClipService(DiskSegmentRingBuffer(root / "ring"), assembler,
                                       output_directory=root / "events", notifier=notifier,
                                       chat_ids=("test-chat",), telegram_enabled=True)
            try:
                request = EventClipRequest("event", datetime.now(timezone.utc), "cat_detected", "test")
                clip = SimpleNamespace(actual_duration_seconds=15)
                with patch("meowkeeper.core.media.event_clip.validate_media",
                           return_value=SimpleNamespace(valid=True, telegram_compatible=False)):
                    attempts, error = service._send_with_retries(request, path, "local",
                        semantics=future, clip_result=clip)
                self.assertEqual((attempts, error), (1, None))
                self.assertTrue(future.assert_ready)
                self.assertEqual(waits, [5])
                caption = notifier.send_video.call_args.kwargs["caption"]
                self.assertIn("剛剛可能在行走、玩耍", caption)
                self.assertNotIn("查看影片", caption)
                self.assertEqual(notifier.send_video.call_args.kwargs["width"], 1920)
            finally:
                service.close()

    def test_slow_ai_falls_back_without_waiting_for_provider_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "event.mp4"
            path.write_bytes(b"media")
            notifier = Mock()
            notifier.send_video.return_value = SimpleNamespace(message_id=42)
            service = EventClipService(DiskSegmentRingBuffer(root / "ring"), Mock(),
                                       output_directory=root / "events", notifier=notifier,
                                       chat_ids=("test-chat",), telegram_enabled=True,
                                       semantic_wait_seconds=.03)
            future = Future()
            try:
                request = EventClipRequest("event", datetime.now(timezone.utc), "cat_detected", "test")
                validation = SimpleNamespace(valid=True, telegram_compatible=True, width=320, height=240,
                                              duration_seconds=15)
                started = time.monotonic()
                with patch("meowkeeper.core.media.event_clip.validate_media", return_value=validation):
                    service._send_with_retries(request, path, "local", semantics=future,
                                              clip_result=SimpleNamespace(actual_duration_seconds=15))
                self.assertLess(time.monotonic() - started, .25)
                self.assertFalse(future.done())
                self.assertEqual("local", notifier.send_video.call_args.kwargs["caption"])
            finally:
                service.close()

    def test_wait_above_hard_limit_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            EventClipService(DiskSegmentRingBuffer(Path(folder) / "ring"), Mock(),
                             output_directory=Path(folder) / "events", semantic_wait_seconds=10.01)

    def test_candidate_api_requires_login_and_does_not_override_identity(self):
        from fastapi.testclient import TestClient

        from meowkeeper.app.web.api import create_app
        from meowkeeper.app.web.state import CoreStateFacade

        with tempfile.TemporaryDirectory() as folder:
            store = SQLiteStore(Path(folder) / "events.db")
            try:
                store.create_media_event(event_id="event", event_type="cat_detected",
                                         trigger_at=datetime.now(timezone.utc), source_id="test",
                                         track_id=None, pet_id="unknown", confidence=.8,
                                         requested_pre_seconds=5, requested_post_seconds=10)
                candidate = SemanticResult("completed", (("scene", ("resting",)),))
                store.record_event_semantics("event", candidate)
                app = create_app(token="a" * 32, facade=CoreStateFacade(store=store,
                                 event_directory=Path(folder) / "events"))
                with TestClient(app) as client:
                    route = "/api/v1/events/event/semantic"
                    self.assertEqual(client.get(route).status_code, 401)
                    response = client.get(route, headers={"Authorization": "Bearer " + "a" * 32})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), candidate.payload())
                self.assertEqual(store.get_media_event("event")["pet_id"], "unknown")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
