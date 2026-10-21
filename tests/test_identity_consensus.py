import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from meowkeeper.core.identity.base import IdentityPrediction
from meowkeeper.core.identity.temporal_identity import TemporalIdentity


class IdentityConsensusTests(unittest.TestCase):
    def prediction(self, pet="a"):
        return IdentityPrediction(pet, pet, .9, .1, .8, .1, "fixture")

    def test_unknown_denominator_and_independent_frames(self):
        temporal = TemporalIdentity()
        now = datetime(2026, 10, 14, tzinfo=timezone.utc)
        for index, pet in enumerate(("a", "a", None, "a", "a")):
            result = temporal.update("scope:track", self.prediction(pet),
                                     captured_at=now+timedelta(seconds=index), frame_id=str(index))
        self.assertEqual(result.valid_frames, 5)
        self.assertEqual(result.vote_fraction, .8)
        self.assertTrue(result.stable)
        repeated = temporal.update("scope:track", self.prediction(), captured_at=now+timedelta(seconds=4), frame_id="4")
        self.assertEqual(repeated.valid_frames, 5)

    def test_duplicate_insufficient_and_track_conflict(self):
        temporal = TemporalIdentity()
        now = datetime(2026, 10, 14, tzinfo=timezone.utc)
        for index in range(5):
            result = temporal.update("a", self.prediction(), captured_at=now+timedelta(seconds=index), frame_id="same")
        self.assertEqual(result.valid_frames, 1)
        self.assertTrue(result.is_unknown)
        self.assertEqual(result.unknown_reason, "insufficient_evidence")
        result = temporal.update("a", self.prediction(), conflict=True)
        self.assertEqual(result.unknown_reason, "track_ambiguous")

    def test_previous_stable_is_not_reused_for_unknown_window(self):
        temporal = TemporalIdentity()
        for _ in range(5):
            temporal.update("a", self.prediction())
        for _ in range(12):
            result = temporal.update("a", self.prediction(None))
        self.assertTrue(result.is_unknown)
        self.assertFalse(result.stable)

    def test_median_score_and_gallery_change_reset(self):
        temporal = TemporalIdentity()
        now = datetime(2026, 10, 14, tzinfo=timezone.utc)
        for index, score in enumerate((.7, .8, .9, .95, .99)):
            prediction = replace(self.prediction(), similarity=score,
                                 candidates=({"pet_id": "a", "gallery_version": "v1"},))
            result = temporal.update("track", prediction,
                                     captured_at=now+timedelta(seconds=index), frame_id=str(index))
        self.assertTrue(result.stable)
        self.assertEqual(result.similarity, .9)
        result = temporal.update("track", replace(prediction,
            candidates=({"pet_id": "a", "gallery_version": "v2"},)),
            captured_at=now+timedelta(seconds=6), frame_id="new")
        self.assertFalse(result.stable)
        self.assertEqual(result.valid_frames, 1)
