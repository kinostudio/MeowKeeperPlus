from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from meowkeeper.core.behavior.models import BehaviorEvent
from meowkeeper.core.identity.feedback_learning import (
    FeedbackIdentityLearner,
    FeedbackLearningPolicy,
    behavior_feedback_report,
    save_track_evidence,
)
from meowkeeper.core.identity.pet_registry import PetRegistry
from meowkeeper.core.tracking import BoundingBox, TrackObservation
from meowkeeper.domain.behavior import BehaviorState, MotionState
from meowkeeper.storage import SQLiteStore
from meowkeeper.storage.database import SCHEMA_VERSION


class Embedder:
    model_version = "test-feedback-v1"

    def embed(self, crop):
        return np.array([0, 1], dtype=np.float32)


class FeedbackLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = SQLiteStore(root / "test.sqlite3")
        self.addCleanup(self.store.close)
        self.registry = PetRegistry(root / "pets")
        self.registry.register_pet("測試貓", pet_id="a")
        self.registry.add_embedding("a", [1, 0], model_version=Embedder.model_version)
        now = datetime.now(timezone.utc)
        self.event = BehaviorEvent(
            pet_id=None, track_id="t1", behavior_state=BehaviorState.RESTING,
            motion_state=MotionState.STATIONARY, event_tags=(), started_at=now,
            ended_at=now, raw_confidence=0.8, smoothed_confidence=0.8,
            inference_method="rule", model_version="test", rule_profile_version="test",
            source_id="test", camera_preset=None,
        )
        self.store.save_behavior_event(self.event)
        self.crop = (Path(__file__).parent / "assets" / "cat.jpg").read_bytes()
        self.obs = TrackObservation("t1", BoundingBox(0, 0, 300, 300), now, .9, "test", 640, 480, self.crop)
        self.learner = FeedbackIdentityLearner(self.store, self.registry, Embedder())

    def labels(self, reviewer="reviewer", pet="a", cat="cat", consent=True):
        # Isolate learning tests. Receipt/authentication guards have separate Telegram tests.
        for dimension, label in (("cat_presence", cat), ("identity", pet)):
            self.store._write(
                "INSERT OR REPLACE INTO behavior_event_annotations(event_id,reviewer_ref,dimension,label,learning_consent) VALUES(?,?,?,?,?)",
                (self.event.event_id, reviewer, dimension, label, int(consent)),
            )

    def test_learn_then_retract_without_changing_manual_enrollment(self):
        original = hashlib.sha256((self.registry.root_dir / "a/embeddings.npz").read_bytes()).hexdigest()
        self.assertTrue(save_track_evidence(self.store, self.event, self.obs))
        self.assertEqual(self.learner.refresh()["published_samples"], 0)
        self.labels()
        self.assertEqual(self.learner.refresh()["published_samples"], 1)
        self.assertEqual(len(self.registry.get_embeddings("a")[0]), 2)
        self.assertEqual(len(self.registry.get_embeddings("a", include_feedback=False)[0]), 1)
        self.labels(cat="not_cat")
        self.assertEqual(self.learner.refresh()["published_samples"], 0)
        self.assertEqual(len(self.registry.get_embeddings("a")[0]), 1)
        self.assertEqual(self.registry.get_pet("a").embedding_count, 1)
        self.assertEqual(hashlib.sha256((self.registry.root_dir / "a/embeddings.npz").read_bytes()).hexdigest(), original)

    def test_identity_label_requires_separate_learning_consent(self):
        save_track_evidence(self.store, self.event, self.obs)
        self.labels(consent=False)
        self.assertEqual(self.learner.refresh()["published_samples"], 0)
        state = self.store.connection.execute("SELECT learning_status FROM identity_feedback_evidence WHERE event_id=?", (self.event.event_id,)).fetchone()[0]
        self.assertEqual(state, "awaiting_learning_consent")
        self.labels(consent=True)
        self.assertEqual(self.learner.refresh()["published_samples"], 1)
        self.labels(consent=False)
        self.assertEqual(self.learner.refresh()["published_samples"], 0)

    def test_conflicting_reviewers_and_model_mismatch_never_learn(self):
        save_track_evidence(self.store, self.event, self.obs)
        self.labels()
        self.labels("second", "b")
        self.assertEqual(self.learner.refresh()["published_samples"], 0)
        self.store._write("DELETE FROM behavior_event_annotations WHERE reviewer_ref='second'", ())
        self.learner.embedder.model_version = "different"
        self.assertEqual(self.learner.refresh()["published_samples"], 0)

    def test_overlap_wrong_track_and_wrong_time_rejected(self):
        other = replace(self.obs, track_id="t2")
        self.assertFalse(save_track_evidence(self.store, self.event, self.obs, observations=[other]))
        self.assertFalse(save_track_evidence(self.store, self.event, other))
        self.assertFalse(save_track_evidence(self.store, replace(self.event, started_at=datetime.max.replace(tzinfo=timezone.utc)), self.obs))

    def test_duplicate_feature_does_not_grow_gallery(self):
        save_track_evidence(self.store, self.event, self.obs)
        self.labels()
        self.learner.embedder.embed = lambda crop: np.array([1, 0], dtype=np.float32)
        self.assertEqual(self.learner.refresh()["published_samples"], 0)

    def test_expired_unreviewed_evidence_removed_without_new_events(self):
        save_track_evidence(self.store, self.event, self.obs)
        self.assertEqual(self.store.connection.execute(
            "SELECT COUNT(*) FROM identity_feedback_evidence").fetchone()[0], 1)
        self.store._write("UPDATE identity_feedback_evidence SET created_at='2000-01-01'", ())
        self.assertEqual(self.learner.refresh()["published_samples"], 0)
        self.assertEqual(self.store.connection.execute(
            "SELECT COUNT(*) FROM identity_feedback_evidence").fetchone()[0], 0)
        self.assertEqual(len(self.registry.get_embeddings("a")[0]), 1)

    def test_hash_and_corrupt_cached_features_fail_safely(self):
        save_track_evidence(self.store, self.event, self.obs)
        self.labels()
        self.store._write("UPDATE identity_feedback_evidence SET crop_sha256='invalid'", ())
        self.assertEqual(self.learner.refresh()["published_samples"], 0)
        self.store._write("UPDATE identity_feedback_evidence SET feature_json='broken',feature_model_version=?", (Embedder.model_version,))
        self.assertEqual(self.learner.refresh()["published_samples"], 0)

    def test_behavior_report_does_not_modify_rules_or_train(self):
        self.store._write("INSERT INTO behavior_event_annotations(event_id,reviewer_ref,dimension,label) VALUES(?,?,?,?)", (self.event.event_id, "reviewer", "behavior", "eating"))
        report = behavior_feedback_report(self.store)
        self.assertEqual(report["comparisons"], [{"predicted": "resting", "reported": "eating", "count": 1}])
        self.assertFalse(report["automatic_training"])
        self.assertFalse(report["automatic_rule_changes"])
        self.assertEqual(self.store.schema_version, SCHEMA_VERSION)

    def test_policy_validation(self):
        with self.assertRaises(ValueError):
            FeedbackLearningPolicy(max_evidence=0)

    def test_capacity_and_migration_are_bounded_and_repeatable(self):
        policy = FeedbackLearningPolicy(max_evidence=1)
        self.assertTrue(save_track_evidence(self.store, self.event, self.obs, policy))
        second = replace(self.event, event_id="second-event")
        self.store.save_behavior_event(second)
        self.assertTrue(save_track_evidence(self.store, second, self.obs, policy))
        self.store.initialize()
        self.store.initialize()
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM identity_feedback_evidence").fetchone()[0], 1)
        self.assertEqual(self.store.schema_version, SCHEMA_VERSION)

    def test_feature_budget_is_one_per_refresh(self):
        save_track_evidence(self.store, self.event, self.obs)
        self.labels()
        second = replace(self.event, event_id="second-event")
        self.store.save_behavior_event(second)
        save_track_evidence(self.store, second, self.obs)
        for dimension, label in (("cat_presence", "cat"), ("identity", "a")):
            self.store._write("INSERT INTO behavior_event_annotations(event_id,reviewer_ref,dimension,label,learning_consent) VALUES(?,?,?,?,?)", (second.event_id, "reviewer", dimension, label, 1))
        self.assertEqual(self.learner.refresh()["new_features"], 1)
        self.assertEqual(self.learner.refresh()["new_features"], 1)
        self.assertEqual(len(self.registry.get_embeddings("a")[0]), 2)

    def test_protected_evidence_survives_expiry_and_new_unreviewed_crops(self):
        save_track_evidence(self.store, self.event, self.obs)
        self.labels()
        self.learner.refresh()
        original = self.store.connection.execute(
            "SELECT feature_json FROM identity_feedback_evidence WHERE event_id=?",
            (self.event.event_id,),
        ).fetchone()[0]
        self.store._write(
            "UPDATE identity_feedback_evidence SET created_at=datetime('now','-4000 days') "
            "WHERE event_id=?", (self.event.event_id,),
        )
        newer = replace(self.event, event_id="new-unreviewed")
        self.store.save_behavior_event(newer)
        save_track_evidence(self.store, newer, self.obs, FeedbackLearningPolicy(max_evidence=1))
        self.learner.refresh()
        row = self.store.connection.execute(
            "SELECT feature_json,learning_status FROM identity_feedback_evidence WHERE event_id=?",
            (self.event.event_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], original)
        self.assertEqual(row[1], "eligible")
        self.assertEqual(self.registry.gallery_summary("a")["feedback_feature_count"], 1)

    def test_pending_manual_label_survives_expiry_without_rewriting_evidence_time(self):
        save_track_evidence(self.store, self.event, self.obs)
        self.labels(consent=False)
        self.store._write(
            "UPDATE identity_feedback_evidence SET created_at='2000-01-01 00:00:00' WHERE event_id=?",
            (self.event.event_id,),
        )
        self.learner.refresh(upgrade_pending_consent=True)
        row = self.store.connection.execute(
            "SELECT created_at,learning_status FROM identity_feedback_evidence WHERE event_id=?",
            (self.event.event_id,),
        ).fetchone()
        self.assertEqual(tuple(row), ("2000-01-01 00:00:00", "eligible"))

    def test_readonly_report_cli_has_no_private_identifiers(self):
        script = Path(__file__).resolve().parents[1] / "tools/report_feedback_learning.py"
        result = subprocess.run([sys.executable, str(script), "--database", str(self.store.path)],
                                capture_output=True, text=True, encoding="utf-8", timeout=15, check=True)
        report = json.loads(result.stdout)
        self.assertFalse(report["automatic_training"])
        self.assertNotIn(self.event.event_id, result.stdout)
        self.assertNotIn("測試貓", result.stdout)


if __name__ == "__main__":
    unittest.main()
