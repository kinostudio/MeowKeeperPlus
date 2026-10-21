"""同時活動軌跡身分衝突須對稱撤除，不覆寫原始推論。"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from meowkeeper.core.events.v02_pipeline import V02AnalysisPipeline
from meowkeeper.core.identity.background_resolver import IdentityResolution
from meowkeeper.core.identity.base import IdentityPrediction
from meowkeeper.core.identity.temporal_identity import TemporalIdentity


class IdentityTrackCollisionTests(TestCase):
    def pipeline(self, *, peer_active=True):
        pipeline = V02AnalysisPipeline.__new__(V02AnalysisPipeline)
        pipeline._generation = 1
        pipeline._source_id = "fixture"
        pipeline._identity_cache = {}
        pipeline._stable_identity = {"first": IdentityPrediction(
            "pet-a", "測試貓", .9, .1, .8, .1, "fixture", stable=True)}
        pipeline.temporal_identity = TemporalIdentity()
        pipeline.store = Mock()
        pipeline.metrics = Mock()
        pipeline.logger = None
        pipeline.identity_classifier = SimpleNamespace(embedder=SimpleNamespace())
        pipeline.behavior_engine = Mock()
        events = {"second": SimpleNamespace(event_id="event-2", started_at=datetime(2026, 1, 1, tzinfo=timezone.utc))}
        if peer_active:
            events["first"] = SimpleNamespace(event_id="event-1", started_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        pipeline.behavior_engine.active_event.side_effect = events.get
        pipeline.behavior_engine.update_identity.return_value = None
        return pipeline

    def test_collision_retracts_both_tracks_and_appends_evidence(self):
        pipeline = self.pipeline()
        original = pipeline._stable_identity["first"]
        now = datetime(2026, 10, 17, tzinfo=timezone.utc)
        pipeline._apply_identity_resolution(IdentityResolution("second", original, now, "fixture", 1, now, "f1"))
        for track in ("first", "second"):
            result = pipeline._stable_identity[track]
            self.assertIsNone(result.predicted_pet_id)
            self.assertFalse(result.stable)
            self.assertEqual(result.unknown_reason, "track_ambiguous")
        self.assertEqual(original.predicted_pet_id, "pet-a")  # immutable original remains intact
        self.assertEqual(pipeline.store.record_identity_evidence.call_count, 2)
        self.assertEqual({call.args[0] for call in pipeline.store.record_identity_evidence.call_args_list}, {"event-1", "event-2"})
        for call in pipeline.store.resolve_media_identity.call_args_list:
            self.assertIsNone(call.kwargs["pet_id"])
        self.assertEqual(pipeline.store.resolve_media_identity.call_count, 2)
        self.assertNotIn(("event-1", "first"), pipeline.temporal_identity._states)
        self.assertNotIn(("event-2", "second"), pipeline.temporal_identity._states)

    def test_inactive_peer_and_stale_source_are_not_retracted(self):
        pipeline = self.pipeline(peer_active=False)
        original = pipeline._stable_identity["first"]
        now = datetime(2026, 10, 17, tzinfo=timezone.utc)
        pipeline._apply_identity_resolution(IdentityResolution("second", original, now, "old-source", 1, now, "f0"))
        pipeline.store.record_identity_evidence.assert_not_called()
        pipeline._apply_identity_resolution(IdentityResolution("second", original, now, "fixture", 1, now, "f1"))
        self.assertEqual(pipeline._stable_identity["first"], original)
        self.assertEqual(pipeline.store.record_identity_evidence.call_count, 1)
        self.assertEqual(pipeline._stable_identity["second"].unknown_reason, "insufficient_evidence")
