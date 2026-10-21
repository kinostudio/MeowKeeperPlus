from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

from meowkeeper.core.behavior.models import BehaviorEvent
from meowkeeper.core.identity import DinoV2Embedder, DINOv2Unavailable, IdentityClassifier
from meowkeeper.core.identity.feedback_learning import FeedbackIdentityLearner, save_track_evidence
from meowkeeper.core.identity.pet_registry import PetRegistry
from meowkeeper.core.tracking import BoundingBox, TrackObservation
from meowkeeper.domain.behavior import BehaviorState, MotionState
from meowkeeper.runtime import validate_identity_model_file
from meowkeeper.storage import SQLiteStore


class RealIdentityModelTests(unittest.TestCase):
    def test_manifest_and_real_dinov2_embedding(self) -> None:
        validate_identity_model_file()
        try:
            embedder = DinoV2Embedder()
        except DINOv2Unavailable as exc:
            self.skipTest(f"官方 DINOv2 runtime 在此環境未載入：{exc}")
        vector = embedder.embed((Path(__file__).parent / "assets" / "cat.jpg").read_bytes())
        self.assertEqual(vector.shape, (384,))
        self.assertAlmostEqual(float(np.linalg.norm(vector)), 1.0, places=3)
        self.assertIn(embedder.backend_name, {"CPU", "PyTorch CUDA"})
        self.assertIsNotNone(embedder.last_latency_ms)
        # Reuse the already-loaded real model; no second identity model/process.
        with tempfile.TemporaryDirectory() as directory:
            registry = PetRegistry(Path(directory) / "pets")
            registry.register_pet("公開素材測試貓", pet_id="public-cat")
            registry.add_embedding("public-cat", vector, model_version=embedder.model_version)
            with Image.open(Path(__file__).parent / "assets" / "cat.jpg") as image:
                output = BytesIO()
                image.transpose(Image.Transpose.FLIP_LEFT_RIGHT).save(output, format="JPEG")
            crop = output.getvalue()
            now = datetime.now(timezone.utc)
            event = BehaviorEvent(
                None, "test-track", BehaviorState.RESTING, MotionState.STATIONARY, (),
                now, now, .9, .9, "rule", "test", "test", "public-test", None,
            )
            store = SQLiteStore(Path(directory) / "test.sqlite3")
            try:
                store.save_behavior_event(event)
                observation = TrackObservation("test-track", BoundingBox(0, 0, 300, 300), now, .9, "public-test", 640, 480, crop)
                self.assertTrue(save_track_evidence(store, event, observation))
                for dimension, label in (("cat_presence", "cat"), ("identity", "public-cat")):
                    store._write("INSERT INTO behavior_event_annotations(event_id,reviewer_ref,dimension,label,learning_consent) VALUES(?,?,?,?,?)", (event.event_id, "test-reviewer", dimension, label, 1))
                result = FeedbackIdentityLearner(store, registry, embedder).refresh()
                self.assertEqual(result["new_features"], 1)
                self.assertEqual(result["published_samples"], 1)
                vectors, _ = registry.get_embeddings("public-cat")
                self.assertEqual(vectors.shape, (2, 384))
                self.assertAlmostEqual(float(np.linalg.norm(vectors[1])), 1, places=3)
                prediction = IdentityClassifier(registry, embedder).classify(crop, track_id="test-track")
                self.assertEqual(prediction.predicted_pet_id, "public-cat")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
