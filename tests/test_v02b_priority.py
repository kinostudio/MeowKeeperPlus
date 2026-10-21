from __future__ import annotations

import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from meowkeeper.core.behavior import (
    BehaviorConfig,
    BehaviorEngine,
    BehaviorFeatureExtractor,
    RuleBehaviorClassifier,
    TrackFeature,
    ZoneDefinition,
    ZoneRegistry,
)
from meowkeeper.core.events.v02_pipeline import V02AnalysisPipeline
from meowkeeper.core.identity import IdentityPrediction, TemporalIdentity
from meowkeeper.core.inference import (
    GPUInferenceScheduler,
    GPUResourceMonitor,
    InferenceDeferred,
    InferencePriority,
    InferenceQueueFull,
)
from meowkeeper.core.media import CompressedRingBuffer, LatestFrameQueue
from meowkeeper.core.media.sources import FramePacket
from meowkeeper.core.notifications import NotificationDispatcher
from meowkeeper.core.tracking import BoundingBox, TrackHistory, TrackObservation
from meowkeeper.domain.behavior import BehaviorState, MotionState
from meowkeeper.storage import SQLiteStore


class PriorityAndLateIdentityTests(unittest.TestCase):
    def test_anonymous_track_can_create_eating_behavior(self) -> None:
        feature = TrackFeature(
            track_id=1,
            pet_id=None,
            observed_at=datetime.now(timezone.utc),
            source_id="test",
            motion_state=MotionState.STATIONARY,
            speed_px_per_sec=0,
            acceleration_px_per_sec2=0,
            vertical_speed_px_per_sec=0,
            direction_changes=0,
            dwell_seconds=12,
            active_zone_types=("food",),
            zone_dwell_seconds={"food": 12},
        )
        result = RuleBehaviorClassifier(BehaviorConfig()).classify(feature)
        self.assertEqual(result.behavior_state, BehaviorState.EATING)

    def test_identity_is_late_bound_to_same_event(self) -> None:
        crop = (Path(__file__).parent / "assets" / "cat.jpg").read_bytes()
        release = threading.Event()

        class Identity:
            class Embedder:
                backend_name = "mock"
                model_version = "mock"
                last_latency_ms = 1.0

            embedder = Embedder()

            def classify(self, _crop: bytes, *, track_id: int | str | None = None) -> IdentityPrediction:
                release.wait(timeout=2)
                return IdentityPrediction(
                    "pet_a", "任意寵物", 0.9, 0.1, 0.8, 0.1, "mock", track_id
                )

        class Tracker:
            def update(self, frame: FramePacket) -> list[TrackObservation]:
                return [
                    TrackObservation(
                        1,
                        BoundingBox(10, 10, 110, 110),
                        frame.captured_at,
                        0.9,
                        "test",
                        640,
                        480,
                        crop,
                    )
                ]

            def reset(self, reason: str = "manual") -> None:
                del reason

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "events.sqlite3")
            history = TrackHistory()
            zones = ZoneRegistry([ZoneDefinition.rectangle("food", "飯碗", "food", 0, 0, 1, 1)])
            pipeline = V02AnalysisPipeline(
                Tracker(),
                Identity(),
                temporal_identity=TemporalIdentity(minimum_unique_frames=1, minimum_time_bins=1),
                history=history,
                feature_extractor=BehaviorFeatureExtractor(history, zones),
                behavior_engine=BehaviorEngine(
                    BehaviorConfig(minimum_eating_seconds=0, state_switch_minimum_seconds=0)
                ),
                store=store,
            )
            started = datetime(2026, 1, 1, tzinfo=timezone.utc)
            try:
                pipeline.process(FramePacket(1, started, "test", crop))
                row = store.connection.execute(
                    "SELECT event_id, pet_id, identity_status FROM behavior_events"
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertIsNone(row["pet_id"])
                self.assertEqual(row["identity_status"], "unknown")
                evidence = store.connection.execute(
                    "SELECT crop_jpeg FROM identity_feedback_evidence WHERE event_id=?",
                    (row["event_id"],),
                ).fetchone()
                self.assertIsNotNone(evidence, "Telegram 未啟用仍須保存本機逐貓裁切")
                self.assertEqual(bytes(evidence["crop_jpeg"]), crop)
                release.set()
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    pipeline.process(
                        FramePacket(2, started + timedelta(seconds=1), "test", crop)
                    )
                    row = store.connection.execute(
                        "SELECT event_id, pet_id, identity_status FROM behavior_events"
                    ).fetchone()
                    if row["pet_id"] == "pet_a":
                        break
                    time.sleep(0.01)
                self.assertEqual(row["pet_id"], "pet_a")
                self.assertEqual(row["identity_status"], "resolved")
                correction = store.connection.execute(
                    "SELECT event_id, predicted_pet_id, corrected_pet_id FROM identity_corrections"
                ).fetchone()
                self.assertEqual(correction["event_id"], row["event_id"])
                self.assertIsNone(correction["predicted_pet_id"])
                self.assertEqual(correction["corrected_pet_id"], "pet_a")
            finally:
                pipeline.close(wait=True)
                store.close()

    def test_scheduler_prioritizes_high_and_deduplicates(self) -> None:
        scheduler = GPUInferenceScheduler(max_pending_tasks=2, auto_start=False)
        order: list[str] = []
        low = scheduler.submit(
            lambda: order.append("low"),
            priority=InferencePriority.LOW,
            dedupe_key="track:1",
        )
        duplicate = scheduler.submit(
            lambda: order.append("duplicate"),
            priority=InferencePriority.LOW,
            dedupe_key="track:1",
        )
        high = scheduler.submit(
            lambda: order.append("high"),
            priority=InferencePriority.HIGH,
        )
        scheduler.start()
        self.assertIs(low, duplicate)
        self.assertEqual(high.result(timeout=2), None)
        self.assertEqual(low.result(timeout=2), None)
        self.assertEqual(order, ["high", "low"])
        scheduler.shutdown(wait=True)

    def test_scheduler_drops_low_work_when_resource_is_critical(self) -> None:
        monitor = GPUResourceMonitor(provider=lambda: {"temperature_c": 95})
        scheduler = GPUInferenceScheduler(resource_monitor=monitor)
        low = scheduler.submit(lambda: "low", priority=InferencePriority.LOW)
        with self.assertRaises(InferenceDeferred):
            low.result(timeout=2)
        high = scheduler.submit(lambda: "high", priority=InferencePriority.HIGH)
        self.assertEqual(high.result(timeout=2), "high")
        scheduler.shutdown(wait=True)

    def test_gpu_oom_can_enter_recovery_without_stopping_scheduler_contract(self) -> None:
        monitor = GPUResourceMonitor(provider=lambda: {"temperature_c": 60})
        monitor.record_error(RuntimeError("CUDA out of memory"))
        self.assertEqual(monitor.state.value, "CRITICAL")
        monitor.recover()
        self.assertEqual(monitor.state.value, "RECOVERY")
        self.assertEqual(monitor.poll(force=True).state.value, "NORMAL")

    def test_queue_full_does_not_raise_in_submit(self) -> None:
        scheduler = GPUInferenceScheduler(max_pending_tasks=1, auto_start=False)
        scheduler.submit(lambda: "first", priority=InferencePriority.HIGH)
        dropped = scheduler.submit(lambda: "second", priority=InferencePriority.HIGH)
        with self.assertRaises(InferenceQueueFull):
            dropped.result()
        scheduler.shutdown(wait=True)

    def test_recording_buffer_keeps_compressed_segments_while_ai_queue_drops_old(self) -> None:
        started = datetime(2026, 1, 1, tzinfo=timezone.utc)
        recording = CompressedRingBuffer(max_seconds=3)
        for index in range(5):
            recording.append(started + timedelta(seconds=index), b"segment", duration_seconds=1)
        self.assertEqual(recording.duration_seconds, 3)
        self.assertEqual(len(recording.snapshot(3)), 3)

        ai_queue: LatestFrameQueue[int] = LatestFrameQueue()
        ai_queue.put(1)
        ai_queue.put(2)
        self.assertEqual(ai_queue.get_nowait(), 2)
        self.assertEqual(ai_queue.dropped_frames, 1)

    def test_notification_dispatch_is_non_blocking(self) -> None:
        release = threading.Event()
        dispatcher = NotificationDispatcher()
        started = time.perf_counter()
        future = dispatcher.submit(lambda: (release.wait(timeout=2), "sent")[1])
        self.assertLess(time.perf_counter() - started, 0.1)
        self.assertFalse(future.done())
        release.set()
        self.assertEqual(future.result(timeout=2), "sent")
        dispatcher.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
