from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np

from meowkeeper.core.analytics import PerformanceMetrics
from meowkeeper.core.audio import AudioVisualFusion, FusionEvidence, RetchingCandidateClassifier
from meowkeeper.core.behavior import (
    BehaviorConfig,
    BehaviorEngine,
    BehaviorEventMerger,
    BehaviorFeatureExtractor,
    RuleBehaviorClassifier,
    TrackFeature,
    ZoneDefinition,
    ZoneRegistry,
)
from meowkeeper.core.events.v02_pipeline import V02AnalysisPipeline
from meowkeeper.core.identity import (
    IdentityClassifier,
    IdentityPrediction,
    IdentityThresholds,
    PetRegistry,
    TemporalIdentity,
)
from meowkeeper.core.media.sources import FramePacket
from meowkeeper.core.tracking import BoundingBox, TrackHistory, TrackObservation
from meowkeeper.domain.behavior import BehaviorState, EventTag, MotionState
from meowkeeper.storage import SQLiteStore


class _MockEmbedder:
    model_version = "mock-identity"
    backend_name = "mock"

    def __init__(self) -> None:
        self.vectors: dict[bytes, np.ndarray] = {}

    def embed(self, crop: bytes) -> np.ndarray:
        return self.vectors[crop]


class IdentityAndBehaviorTests(unittest.TestCase):
    def test_close_identity_scores_can_surface_as_unconfirmed_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = PetRegistry(directory)
            first = registry.register_pet("Alpha", pet_id="pet_alpha")
            second = registry.register_pet("Beta", pet_id="pet_beta")
            embedder = _MockEmbedder()
            embedder.vectors[b"query"] = np.array([1.0, 0.0], dtype=np.float32)
            for profile, score in ((first, 0.62234), (second, 0.54570)):
                registry.add_embedding(
                    profile.pet_id,
                    [score, float(np.sqrt(1.0 - score**2))],
                    model_version=embedder.model_version,
                )

            classifier = IdentityClassifier(registry, embedder)
            with patch("meowkeeper.core.identity.identity_classifier.extract_hsv_histogram", return_value=None):
                candidate = classifier.classify(b"query", track_id="track-1")

            self.assertEqual(candidate.predicted_pet_id, "pet_alpha")
            self.assertFalse(candidate.stable)
            self.assertAlmostEqual(candidate.confidence_margin, 0.07664, places=4)
            self.assertEqual(IdentityThresholds().minimum_margin, 0.05)
            self.assertEqual(IdentityThresholds().accepted_threshold, 0.62)
            self.assertEqual(IdentityThresholds().unknown_threshold, 0.45)

    def test_identity_margin_below_new_candidate_gate_stays_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = PetRegistry(directory)
            first = registry.register_pet("Alpha", pet_id="pet_alpha")
            second = registry.register_pet("Beta", pet_id="pet_beta")
            embedder = _MockEmbedder()
            embedder.vectors[b"query"] = np.array([1.0, 0.0], dtype=np.float32)
            for profile, score in ((first, 0.70), (second, 0.66)):
                registry.add_embedding(
                    profile.pet_id,
                    [score, float(np.sqrt(1.0 - score**2))],
                    model_version=embedder.model_version,
                )

            classifier = IdentityClassifier(registry, embedder)
            with patch("meowkeeper.core.identity.identity_classifier.extract_hsv_histogram", return_value=None):
                prediction = classifier.classify(b"query", track_id="track-2")

            self.assertTrue(prediction.is_unknown)
            self.assertEqual(prediction.unknown_reason, "small_margin")

    def test_single_pet_identity_still_requires_calibration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = PetRegistry(directory)
            profile = registry.register_pet("Alpha", pet_id="pet_alpha")
            embedder = _MockEmbedder()
            embedder.vectors[b"query"] = np.array([1.0, 0.0], dtype=np.float32)
            registry.add_embedding(profile.pet_id, [1.0, 0.0], model_version=embedder.model_version)
            classifier = IdentityClassifier(registry, embedder, strict_single_pet=True)
            with patch("meowkeeper.core.identity.identity_classifier.extract_hsv_histogram", return_value=None):
                prediction = classifier.classify(b"query", track_id="track-3")

            self.assertTrue(prediction.is_unknown)
            self.assertEqual(prediction.unknown_reason, "calibration_required")

    def test_arbitrary_pet_registration_known_and_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = PetRegistry(directory)
            first = registry.register_pet("Alpha", pet_id="pet_alpha")
            second = registry.register_pet("Beta", pet_id="pet_beta")
            embedder = _MockEmbedder()
            embedder.vectors[b"alpha"] = np.array([1.0, 0.0], dtype=np.float32)
            embedder.vectors[b"beta"] = np.array([0.0, 1.0], dtype=np.float32)
            embedder.vectors[b"unknown"] = np.array([1.0, 1.0], dtype=np.float32)
            registry.add_embedding(first.pet_id, [1.0, 0.0], model_version=embedder.model_version)
            registry.add_embedding(second.pet_id, [0.0, 1.0], model_version=embedder.model_version)
            classifier = IdentityClassifier(registry, embedder)
            known = classifier.classify(b"alpha", track_id=1)
            unknown = classifier.classify(b"unknown", track_id=1)
            self.assertEqual(known.predicted_pet_id, "pet_alpha")
            self.assertTrue(unknown.is_unknown)

    def test_temporal_identity_does_not_switch_on_one_bad_sample(self) -> None:
        thresholds = IdentityThresholds(temporal_vote_window=5, minimum_valid_samples=2)
        temporal = TemporalIdentity(thresholds)

        def prediction(pet_id: str | None) -> IdentityPrediction:
            return IdentityPrediction(pet_id, pet_id, 0.9 if pet_id else 0.1, 0.1, 0.8, 0.1, "mock")

        temporal.update(3, prediction("pet_a"))
        stable = temporal.update(3, prediction("pet_a"))
        switched = temporal.update(3, prediction("pet_b"))
        self.assertEqual(stable.predicted_pet_id, "pet_a")
        self.assertIsNone(switched.predicted_pet_id)
        self.assertEqual(switched.unknown_reason, "unstable_votes")

    def test_running_is_not_playing_without_play_evidence(self) -> None:
        classifier = RuleBehaviorClassifier(BehaviorConfig())
        feature = TrackFeature(
            track_id=1,
            pet_id="pet_a",
            observed_at=datetime.now(timezone.utc),
            source_id="test",
            motion_state=MotionState.RUNNING,
            speed_px_per_sec=200,
            acceleration_px_per_sec2=10,
            vertical_speed_px_per_sec=0,
            direction_changes=0,
            dwell_seconds=1,
        )
        result = classifier.classify(feature)
        self.assertEqual(result.behavior_state, BehaviorState.OTHER_ACTIVE)

    def test_zones_and_meal_short_interruption_merge(self) -> None:
        zones = ZoneRegistry([ZoneDefinition.rectangle("food", "Food", "food", 0, 0, 1, 1)])
        self.assertEqual(zones.classify_center((0.5, 0.5))[0].zone_id, "food")
        started = datetime(2026, 1, 1, tzinfo=timezone.utc)
        merger = BehaviorEventMerger(default_merge_gap_seconds=30)

        def observation(at: datetime, state: BehaviorState) -> object:
            from meowkeeper.core.behavior.models import BehaviorObservation

            return BehaviorObservation(
                pet_id="pet_a",
                track_id=1,
                behavior_state=state,
                motion_state=MotionState.STATIONARY,
                event_tags=(EventTag.ZONE_ENTER,),
                observed_at=at,
                raw_confidence=0.8,
                smoothed_confidence=0.8,
                inference_method="rules",
                model_version="rules",
                rule_profile_version="test",
                source_id="test",
                camera_preset=None,
            )

        self.assertEqual(merger.ingest(observation(started, BehaviorState.EATING)), ())
        merger.ingest(observation(started + timedelta(seconds=12), BehaviorState.OTHER_ACTIVE))
        self.assertEqual(merger.ingest(observation(started + timedelta(seconds=20), BehaviorState.EATING)), ())
        self.assertEqual(merger.flush()[0].behavior_state, BehaviorState.EATING)

    def test_audio_visual_requires_both_sides_and_is_candidate_only(self) -> None:
        fusion = AudioVisualFusion(minimum_audio=0.6, minimum_visual=0.5, minimum_fused=0.55)
        self.assertFalse(fusion.evaluate(FusionEvidence(0.9, 0.1, 1.0, 1.0, 1.0)))
        result = RetchingCandidateClassifier(fusion).classify(
            observed_at=datetime.now(timezone.utc),
            audio_score=0.9,
            visual_score=0.8,
            temporal_consistency=0.7,
        )
        self.assertTrue(result.is_candidate)
        self.assertEqual(result.label, "possible_vomit")
        self.assertIn("疑似", result.message_zh_tw)

    def test_sqlite_stores_behavior_event_and_label(self) -> None:
        from meowkeeper.core.behavior.models import BehaviorEvent

        with tempfile.TemporaryDirectory() as directory, SQLiteStore(
            Path(directory) / "events.sqlite3"
        ) as store:
                now = datetime.now(timezone.utc)
                event = BehaviorEvent(
                    pet_id="pet_a",
                    track_id=1,
                    behavior_state=BehaviorState.EATING,
                    motion_state=MotionState.STATIONARY,
                    event_tags=(),
                    started_at=now,
                    ended_at=now + timedelta(seconds=20),
                    raw_confidence=0.8,
                    smoothed_confidence=0.75,
                    inference_method="rules",
                    model_version="rules",
                    rule_profile_version="test",
                    source_id="test",
                    camera_preset=None,
                )
                store.save_behavior_event(event)
                store.add_event_label(event.event_id, "eating", is_correct=True)
                names = {
                    row["name"]
                    for row in store.connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertTrue({"pets", "pet_embeddings", "tracks", "behavior_events", "event_labels"} <= names)
                self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM event_labels").fetchone()[0], 1)

    def test_v02_pipeline_reuses_single_tracker_and_does_not_write_each_frame(self) -> None:
        class Tracker:
            def __init__(self, crop: bytes) -> None:
                self.crop = crop
                self.calls = 0

            def update(self, frame: FramePacket) -> list[TrackObservation]:
                self.calls += 1
                return [
                    TrackObservation(
                        track_id=1,
                        bbox=BoundingBox(10, 10, 100, 100),
                        captured_at=frame.captured_at,
                        confidence=0.9,
                        source_id=frame.source_url,
                        frame_width=640,
                        frame_height=480,
                        crop_jpeg=self.crop,
                    )
                ]

            def reset(self, reason: str = "manual") -> None:
                return None

        with tempfile.TemporaryDirectory() as directory:
            crop = (Path(__file__).parent / "assets" / "cat.jpg").read_bytes()
            registry = PetRegistry(Path(directory) / "pets")
            profile = registry.register_pet("Arbitrary", pet_id="pet_a")
            embedder = _MockEmbedder()
            embedder.vectors[crop] = np.array([1.0, 0.0], dtype=np.float32)
            registry.add_embedding(profile.pet_id, [1.0, 0.0], model_version=embedder.model_version)
            classifier = IdentityClassifier(registry, embedder)
            history = TrackHistory()
            engine = BehaviorEngine(BehaviorConfig(state_switch_minimum_seconds=0))
            pipeline = V02AnalysisPipeline(
                Tracker(crop),
                classifier,
                temporal_identity=TemporalIdentity(
                    IdentityThresholds(temporal_vote_window=1, minimum_valid_samples=1)
                ),
                history=history,
                feature_extractor=BehaviorFeatureExtractor(history, ZoneRegistry()),
                behavior_engine=engine,
                store=SQLiteStore(Path(directory) / "events.sqlite3"),
                metrics=PerformanceMetrics(),
            )
            started = datetime(2026, 1, 1, tzinfo=timezone.utc)
            try:
                for index in range(2):
                    pipeline.process(FramePacket(index + 1, started + timedelta(seconds=index / 2), "one-source", crop))
                self.assertEqual(pipeline.tracker.calls, 2)
                self.assertEqual(pipeline.metrics.frames_seen, 2)
                self.assertEqual(pipeline.metrics.identity_inferences, 1)
                self.assertEqual(
                    pipeline.store.connection.execute("SELECT COUNT(*) FROM tracks").fetchone()[0],
                    1,
                )
            finally:
                pipeline.close(wait=True)
                pipeline.store.close()


if __name__ == "__main__":
    unittest.main()
