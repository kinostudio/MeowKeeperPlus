import hashlib
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from meowkeeper.core.behavior import ZoneDefinition
from meowkeeper.core.behavior.scene_views import SceneViewRegistry
from meowkeeper.core.event_semantics import SemanticRequest
from meowkeeper.core.events.v02_host_analyzer import UnknownIdentityClassifier, V02HostAnalyzer
from meowkeeper.core.media.sources import FramePacket
from meowkeeper.core.tracking.base import BoundingBox, TrackObservation
from meowkeeper.storage.database import SCHEMA_VERSION, SQLiteStore
from tools.run_host import build_parser


class FakeTracker:
    inference_backend = "CPU"
    device = "cpu"
    calls = 0

    def update(self, frame):
        self.calls += 1
        return [TrackObservation(1, BoundingBox(10, 10, 60, 60), frame.captured_at,
                                 0.9, frame.source_url, 100, 100)]

    def reset(self, reason):
        pass


class V02HostTests(unittest.TestCase):
    def test_semantic_cache_is_opt_in_and_reuses_existing_track_crops(self):
        class CropTracker(FakeTracker):
            def update(self, frame):
                self.calls += 1
                output = BytesIO()
                Image.new("RGB", (96, 96), (self.calls * 20, 80, 90)).save(output, format="JPEG")
                return [TrackObservation(1, BoundingBox(0, 0, 96, 96), frame.captured_at,
                                         .9, frame.source_url, 100, 100, output.getvalue())]
        tracker = CropTracker()
        analyzer = V02HostAnalyzer(tracker, UnknownIdentityClassifier(), store=None)
        trigger = datetime.now(timezone.utc)
        try:
            self.assertIsNone(analyzer.semantic_buffer)
            analyzer.enable_semantic_frames()
            for index in range(4):
                analysis = analyzer.analyze(FramePacket(index, trigger + timedelta(seconds=index * .3),
                                                        "rtsp://private-example", b"jpeg"))
            self.assertIn("v02_host_adapter_total_ms", analyzer.timings.snapshot())
            pipeline_timings = analyzer.pipeline.timings.snapshot()
            self.assertIn("v02_frame_pipeline_total_ms", pipeline_timings)
            self.assertIn("v02_yolo_scheduler_roundtrip_ms", pipeline_timings)
            request = SemanticRequest("e", b"jpeg", analysis.details["track_ids"], trigger)
            prepared = analyzer.prepare_semantic(request)
            self.assertEqual(tracker.calls, 4)
            self.assertEqual(len(prepared.storyboards), 1)
            self.assertNotIn("rtsp://", prepared.storyboards[0].epoch)
        finally:
            analyzer.close()

    def test_scene_reference_is_added_only_when_explicitly_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "events.db")
            source = "rtsp://user:private@camera/room"
            source_ref = "source-" + hashlib.sha256(source.encode()).hexdigest()[:24]
            reference_bytes = BytesIO()
            Image.new("RGB", (320, 240), (170, 120, 80)).save(reference_bytes, format="JPEG")
            registry = SceneViewRegistry(store)
            view = registry.register(source_ref, "餐廳", reference_bytes.getvalue(), 0, verified_current=False)
            registry.save_object(source_ref, {"object_id":None, "view_id":view, "expected_revision":0,
                "name":"飯碗", "kind":"food", "polygon":[[.1,.1],[.7,.1],[.7,.7],[.1,.7]], "enabled":True})

            class CropTracker(FakeTracker):
                def update(self, frame):
                    self.calls += 1
                    output = BytesIO()
                    Image.new("RGB", (96, 96), (self.calls * 20, 80, 90)).save(output, format="JPEG")
                    return [TrackObservation(1, BoundingBox(0, 0, 96, 96), frame.captured_at,
                                             .9, frame.source_url, 100, 100, output.getvalue())]

            tracker = CropTracker()
            analyzer = V02HostAnalyzer(tracker, UnknownIdentityClassifier(), store=store)
            analyzer.enable_semantic_frames({"include_scene_references":True, "frame_count":4,
                                             "canvas_size":"1280x960", "jpeg_quality":82,
                                             "include_timestamps":True, "include_zones":False})
            trigger = datetime.now(timezone.utc)
            try:
                for index in range(4):
                    analysis = analyzer.analyze(FramePacket(index, trigger + timedelta(seconds=index * .3), source, b""))
                request = SemanticRequest("scene-reference-event", b"event", analysis.details["track_ids"], trigger)
                prepared = analyzer.prepare_semantic(request)
                self.assertEqual(len(prepared.storyboards), 1)
                self.assertIsNotNone(prepared.scene_reference_jpeg)
                self.assertEqual(prepared.scene_reference_labels,
                                 ("Reference 01: 食物／飯碗; view: 餐廳; user label: 飯碗",))
                self.assertNotIn("private", str(prepared.scene_reference_labels))
            finally:
                analyzer.close()
                store.close()

    def test_factory_accepts_external_tracker_proxy_for_process_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            tracker = FakeTracker()
            analyzer = V02HostAnalyzer.create(
                model_path="unused.pt", confidence=0.45, store=None, data_root=directory,
                logger=lambda _message: None, tracker=tracker,
            )
            try:
                self.assertIs(analyzer.tracker.tracker, tracker)
            finally:
                analyzer.close()

    def test_gpu_fallback_updates_backend_and_changes_track_namespace(self):
        tracker = FakeTracker()
        tracker.device = "cuda:0"
        tracker.inference_backend = "PyTorch CUDA"
        analyzer = V02HostAnalyzer(tracker, UnknownIdentityClassifier(), store=None)
        prefix = analyzer.tracker.prefix
        original_update = tracker.update

        def fallback(frame):
            tracker.device = "cpu"
            tracker.inference_backend = "CPU"
            return original_update(frame)

        tracker.update = fallback
        try:
            analyzer.analyze(FramePacket(1, datetime.now(timezone.utc), "test-source", b""))
            self.assertEqual(analyzer.device, "cpu")
            self.assertEqual(analyzer.inference_backend, "CPU")
            self.assertNotEqual(analyzer.tracker.prefix, prefix)
        finally:
            analyzer.close()

    def test_saved_zones_follow_source_without_legacy_leak(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "events.db")
            food = ZoneDefinition.rectangle("food", "飯碗區", "food", 0, 0, 1, 1)
            store.save_zone(food)  # Old zones have no proven source binding.
            source = "rtsp://private:secret@camera/first"
            reference = "source-" + hashlib.sha256(source.encode()).hexdigest()[:24]
            store.replace_source_zones(reference, (food,))
            analyzer = V02HostAnalyzer(FakeTracker(), UnknownIdentityClassifier(), store=store)
            try:
                analyzer.analyze(FramePacket(1, datetime.now(timezone.utc), source, b""))
                # V4.3：來源對應不等於視角校正，舊固定ROI不可直接推論。
                self.assertEqual(analyzer.pipeline.feature_extractor.zones.list(), ())
                analyzer.analyze(FramePacket(2, datetime.now(timezone.utc), "different-camera", b""))
                self.assertEqual(analyzer.pipeline.feature_extractor.zones.list(), ())
                raw = store.connection.execute("SELECT source_ref FROM source_zones").fetchone()[0]
                self.assertEqual(raw, reference)
                self.assertNotIn("secret", raw)
            finally:
                analyzer.close()
                store.close()

    def test_zone_read_failure_isolated_from_cat_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "events.db")
            logs = []
            analyzer = V02HostAnalyzer(FakeTracker(), UnknownIdentityClassifier(), store=store, logger=logs.append)
            try:
                with patch("meowkeeper.core.behavior.scene_views.SceneViewRegistry.state", side_effect=sqlite3.OperationalError("locked")) as read:
                    now = datetime.now(timezone.utc)
                    for index in range(2):
                        self.assertTrue(analyzer.analyze(FramePacket(index, now + timedelta(seconds=index * 3), "source", b"")).triggered)
                    self.assertEqual(read.call_count, 2)
                self.assertEqual(analyzer.pipeline.feature_extractor.zones.list(), ())
                self.assertTrue(any("區域載入失敗" in message for message in logs))
            finally:
                analyzer.close()
                store.close()

    def test_scoped_zone_replace_validation_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.db"
            food = ZoneDefinition.rectangle("food", "飯碗區", "food", 0, 0, 1, 1)
            first, second = "source-" + "a" * 24, "source-" + "b" * 24
            with SQLiteStore(path) as store:
                store.replace_source_zones(first, (food,))
                store.replace_source_zones(second, (food,))
                with self.assertRaises(ValueError):
                    store.replace_source_zones(first, (food, food))
                with self.assertRaises(ValueError):
                    store.replace_source_zones("rtsp://user:secret@camera", ())
                self.assertEqual(store.load_source_zones(first), (food,))
                store.replace_source_zones(first, ())
                self.assertEqual(store.load_source_zones(second), (food,))
            with SQLiteStore(path) as store:
                self.assertEqual(store.schema_version, SCHEMA_VERSION)
                self.assertEqual(store.load_source_zones(first), ())
                self.assertEqual(store.load_source_zones(second), (food,))

    def test_host_cli_explicit_opt_in(self):
        self.assertEqual(build_parser().parse_args(["--analyzer", "v02"]).analyzer, "v02")
        self.assertEqual(build_parser().parse_args([]).analyzer, "auto")

    def test_v4_zone_migration_preserves_unbound_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.db"
            with SQLiteStore(path) as store:
                store.save_zone(ZoneDefinition.rectangle("legacy", "舊飯碗區", "food", 0, 0, 1, 1))
                store.connection.execute("DROP TABLE source_zones")
                store.connection.execute("UPDATE schema_meta SET value='4' WHERE key='schema_version'")
                store.connection.execute("PRAGMA user_version=4")
                store.connection.commit()
            for _ in range(2):
                with SQLiteStore(path) as store:
                    self.assertEqual(store.schema_version, SCHEMA_VERSION)
                    self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM zones").fetchone()[0], 1)
                    self.assertEqual(store.load_source_zones("source-" + "a" * 24), ())

    def test_single_inference_safe_source_and_behavior_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "events.db")
            tracker = FakeTracker()
            analyzer = V02HostAnalyzer(tracker, UnknownIdentityClassifier(), store=store)
            now = datetime.now(timezone.utc)
            try:
                for index in range(4):
                    frame = FramePacket(index, now + timedelta(seconds=index * 3),
                                        "rtsp://private:password@camera/test", b"")
                    analysis = analyzer.analyze(frame)
                    self.assertTrue(analysis.triggered)
                self.assertEqual(tracker.calls, 4)
            finally:
                analyzer.close()
            rows = store.connection.execute("SELECT source_id,track_id FROM behavior_events").fetchall()
            self.assertTrue(rows)
            self.assertTrue(all(row["source_id"].startswith("source-") for row in rows))
            self.assertNotIn("password", str([tuple(r) for r in rows]))
            self.assertIn(":", rows[0]["track_id"])
            store.close()

    def test_database_failure_does_not_discard_current_cat(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "events.db")
            analyzer = V02HostAnalyzer(FakeTracker(), UnknownIdentityClassifier(), store=store)
            frame = FramePacket(1, datetime.now(timezone.utc), "source", b"")
            try:
                with patch.object(store, "save_track", side_effect=sqlite3.OperationalError("locked")), patch.object(store, "save_behavior_event", side_effect=sqlite3.OperationalError("locked")):
                    self.assertTrue(analyzer.analyze(frame).triggered)
            finally:
                analyzer.close()
                store.close()


if __name__ == "__main__":
    unittest.main()
