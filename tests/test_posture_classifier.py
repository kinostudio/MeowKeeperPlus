"""Tests for lightweight static cat posture classifier."""

from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import torch

from meowkeeper.core.behavior.posture_classifier import (
    POSTURE_CLASSES,
    POSTURE_LABELS_ZH,
    PostureClassifier,
    PostureMLP,
    PosturePrediction,
)
from meowkeeper.core.identity.base import IdentityPrediction, IdentityThresholds
from meowkeeper.core.identity.identity_classifier import IdentityClassifier
from meowkeeper.runtime import posture_model_file


class TestPostureClassifier(unittest.TestCase):
    def test_mlp_architecture(self):
        mlp = PostureMLP.build_torch_module(in_features=384, hidden_dim=128, num_classes=4)
        x = torch.randn(2, 384)
        out = mlp(x)
        self.assertEqual(out.shape, (2, 4))

    def test_posture_prediction_dataclass(self):
        pred = PosturePrediction(
            posture="sitting",
            confidence=0.92,
            probabilities={"sitting": 0.92, "standing": 0.03, "lying": 0.03, "unknown": 0.02},
            label_zh="坐著",
            model_version="cat_posture_mlp@v1.0.0",
        )
        self.assertEqual(pred.posture, "sitting")
        self.assertEqual(pred.label_zh, "坐著")
        self.assertTrue(pred.is_known)

        unknown_pred = PosturePrediction(
            posture="unknown",
            confidence=0.30,
            probabilities={"sitting": 0.3, "standing": 0.2, "lying": 0.2, "unknown": 0.3},
            label_zh="未知",
            model_version="cat_posture_mlp@v1.0.0",
        )
        self.assertFalse(unknown_pred.is_known)

    def test_numpy_forward_pass_parity(self):
        clf = PostureClassifier()
        # Set synthetic weights
        rng = np.random.default_rng(123)
        w1 = rng.normal(0, 0.1, size=(384, 128)).astype(np.float32)
        b1 = rng.normal(0, 0.1, size=(128,)).astype(np.float32)
        w2 = rng.normal(0, 0.1, size=(128, 4)).astype(np.float32)
        b2 = rng.normal(0, 0.1, size=(4,)).astype(np.float32)
        clf.set_weights_numpy(w1, b1, w2, b2)

        self.assertTrue(clf.is_ready())
        test_vec = rng.normal(0, 1.0, size=(384,)).astype(np.float32)
        pred = clf.predict_vector(test_vec)
        self.assertIn(pred.posture, POSTURE_CLASSES)
        self.assertGreaterEqual(pred.confidence, 0.0)
        self.assertLessEqual(pred.confidence, 1.0)
        self.assertAlmostEqual(sum(pred.probabilities.values()), 1.0, places=4)

    def test_confidence_threshold_fallback(self):
        clf = PostureClassifier(confidence_threshold=0.999)  # Impractically high threshold
        rng = np.random.default_rng(456)
        w1 = rng.normal(0, 0.1, size=(384, 128)).astype(np.float32)
        b1 = np.zeros(128, dtype=np.float32)
        w2 = rng.normal(0, 0.1, size=(128, 4)).astype(np.float32)
        b2 = np.zeros(4, dtype=np.float32)
        clf.set_weights_numpy(w1, b1, w2, b2)

        vec = rng.normal(0, 1.0, size=(384,)).astype(np.float32)
        pred = clf.predict_vector(vec)
        self.assertEqual(pred.posture, "unknown")
        self.assertEqual(pred.label_zh, "未知")

    def test_real_trained_model_file(self):
        model_path = posture_model_file()
        if not model_path.is_file():
            self.skipTest("posture_mlp_v1.pth not yet created")

        clf = PostureClassifier(model_path)
        self.assertTrue(clf.is_ready())
        self.assertEqual(clf.model_version, "cat_posture_mlp@v1.0.0")

        # Random vector should be unknown
        rng = np.random.default_rng(789)
        rand_vec = rng.normal(0, 1.0, size=384).astype(np.float32)
        pred = clf.predict_vector(rand_vec)
        self.assertIn(pred.posture, POSTURE_CLASSES)
        self.assertLess(clf.last_latency_ms, 2.0)  # Must be faster than 2ms

    def test_identity_classifier_integration(self):
        mock_registry = MagicMock()
        mock_registry.list_pets.return_value = []
        mock_embedder = MagicMock()
        mock_embedder.model_version = "dinov2_vits14@mock"
        # Return a normalized 384 vector
        v = np.ones(384, dtype=np.float32)
        v /= np.linalg.norm(v)
        mock_embedder.embed.return_value = v

        mock_posture = MagicMock()
        expected_pred = PosturePrediction(
            posture="sitting",
            confidence=0.88,
            probabilities={"sitting": 0.88, "standing": 0.04, "lying": 0.04, "unknown": 0.04},
            label_zh="坐著",
            model_version="cat_posture_mlp@v1.0.0",
        )
        mock_posture.predict_vector.return_value = expected_pred

        clf = IdentityClassifier(
            registry=mock_registry,
            embedder=mock_embedder,
            posture_classifier=mock_posture,
        )

        res = clf.classify(b"dummy_jpeg", track_id="track-1")
        self.assertIsNotNone(res.posture)
        self.assertEqual(res.posture.posture, "sitting")
        self.assertEqual(res.posture.label_zh, "坐著")
        mock_posture.predict_vector.assert_called_once()

    def test_model_registry_db_record(self):
        db_path = Path(r"C:\ProgramData\MeowKeeperPlus\Data\meowkeeper.db")
        if not db_path.is_file():
            self.skipTest("Database not found")

        con = sqlite3.connect(str(db_path))
        row = con.execute(
            "SELECT model_version, model_name, backend, status FROM model_registry WHERE model_version=?",
            ("cat_posture_mlp@v1.0.0",),
        ).fetchone()
        con.close()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], "cat_posture_mlp@v1.0.0")
        self.assertEqual(row[1], "cat_posture_mlp")
        self.assertEqual(row[3], "active")


if __name__ == "__main__":
    unittest.main()
