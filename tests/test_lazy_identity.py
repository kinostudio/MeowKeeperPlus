import tempfile
import unittest
from unittest.mock import Mock, patch

from meowkeeper.core.identity.identity_classifier import LazyIdentityClassifier
from meowkeeper.core.identity.pet_registry import PetRegistry


class LazyIdentityTests(unittest.TestCase):
    def test_new_features_are_used_without_restarting_classifier(self):
        with tempfile.TemporaryDirectory() as folder:
            registry = PetRegistry(folder)
            registry.register_pet("測試甲", pet_id="a")
            classifier = LazyIdentityClassifier(registry)
            self.assertEqual(classifier.classify(b"test").unknown_reason, "no_gallery")
            backend = Mock(model_version="fixture", backend_name="CPU")
            backend.embed.return_value = [1., 0.]
            registry.add_embedding("a", [1., 0.], model_version="fixture")
            with patch("meowkeeper.core.identity.dinov2_embedder.DinoV2Embedder", return_value=backend) as loader:
                result = classifier.classify(b"test")
                self.assertEqual(result.candidates[0]["name"], "測試甲")
                self.assertEqual(result.unknown_reason, "calibration_required")
                classifier.classify(b"test")
                loader.assert_called_once_with(device="cpu")

    def test_model_failure_has_finite_retry_cooldown(self):
        with tempfile.TemporaryDirectory() as folder:
            registry = PetRegistry(folder)
            registry.register_pet("測試", pet_id="a")
            registry.add_embedding("a", [1., 0.], model_version="fixture")
            classifier = LazyIdentityClassifier(registry)
            with patch("meowkeeper.core.identity.dinov2_embedder.DinoV2Embedder", side_effect=RuntimeError("unavailable")) as loader:
                self.assertEqual(classifier.classify(b"test").unknown_reason, "model_unavailable")
                self.assertEqual(classifier.classify(b"test").unknown_reason, "model_unavailable")
                self.assertEqual(loader.call_count, 1)
