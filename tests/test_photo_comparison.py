from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from fastapi.testclient import TestClient

from meowkeeper.app.web import CoreStateFacade, create_app
from meowkeeper.core.identity.pet_registry import PetRegistry
from meowkeeper.core.identity.photo_comparison import compare_photo


class PhotoComparisonTests(unittest.TestCase):
    def test_real_dinov2_photo_comparison_with_public_fixture(self):
        from meowkeeper.core.identity.dinov2_embedder import DinoV2Embedder

        crop = (Path(__file__).parent / "assets" / "cat.jpg").read_bytes()
        embedder = DinoV2Embedder(device="cpu")
        with tempfile.TemporaryDirectory() as folder:
            registry = PetRegistry(folder)
            registry.register_pet("公開素材測試貓", pet_id="public-cat")
            registry.add_reference_image("public-cat", crop)
            registry.add_embedding("public-cat", embedder.embed(crop), model_version=embedder.model_version)
            result = compare_photo(registry, embedder, crop)
            self.assertIsNone(result["predicted_pet_id"])
            self.assertEqual(result["unknown_reason"], "calibration_required")
            self.assertGreater(result["candidates"][0]["reference_similarity"], 0.99)
            self.assertIn("/images/", result["candidates"][0]["reference_url"])

    def test_web_auth_csrf_and_ai_result_without_saving_upload(self):
        token = "isolated-comparison-token"
        with tempfile.TemporaryDirectory() as folder:
            core = CoreStateFacade(event_directory=Path(folder) / "events")
            registry = PetRegistry(Path(folder) / "pets")
            registry.register_pet("測試貓", pet_id="test-cat")
            registry.add_embedding("test-cat", [1, 0], model_version="test-v1")
            embedder = Mock(model_version="test-v1", backend_name="test")
            embedder.embed.return_value = np.array([1., 0.])
            crop = (Path(__file__).parent / "assets" / "cat.jpg").read_bytes()
            app = create_app(facade=core, token=token)
            with TestClient(app) as client:
                headers = {"Authorization": "Bearer " + token}
                url = "/api/v1/pets/compare-photo"
                files = {"image": ("cat.jpg", crop, "image/jpeg")}
                self.assertEqual(client.post(url, files=files).status_code, 401)
                self.assertEqual(client.post(url, files=files, headers=headers).status_code, 403)
                headers["X-MeowKeeper-CSRF"] = hashlib.sha256((token + ":csrf").encode()).hexdigest()
                with patch("meowkeeper.core.identity.dinov2_embedder.DinoV2Embedder", return_value=embedder):
                    result = client.post(url, files=files, headers=headers)
                self.assertEqual(result.status_code, 200)
                self.assertIsNone(result.json()["predicted_name"])
                self.assertEqual(result.json()["unknown_reason"], "calibration_required")
                self.assertEqual(registry.get_pet("test-cat").embedding_count, 1)
                app.state.pet_comparison_lock.acquire()
                try:
                    self.assertEqual(client.post(url, files=files, headers=headers).status_code, 429)
                finally:
                    app.state.pet_comparison_lock.release()

    def test_ai_uses_features_once_and_shows_nearest_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            registry = PetRegistry(folder)
            registry.register_pet("貓 A", pet_id="cat-a")
            registry.register_pet("貓 B", pet_id="cat-b")
            crop = (Path(__file__).parent / "assets" / "cat.jpg").read_bytes()
            registry.add_reference_image("cat-a", crop)
            registry.add_reference_image("cat-b", crop)
            registry.add_embedding("cat-a", [1, 0], model_version="test-v1")
            registry.add_embedding("cat-b", [0, 1], model_version="test-v1")
            embedder = Mock(model_version="test-v1", backend_name="test")
            embedder.embed.return_value = np.array([1., 0.])
            result = compare_photo(registry, embedder, crop)
            self.assertEqual(result["predicted_name"], "貓 A")
            self.assertEqual(result["candidates"][0]["name"], "貓 A")
            self.assertIn("/api/v1/pets/cat-a/images/", result["candidates"][0]["reference_url"])
            embedder.embed.assert_called_once()
            embedder.embed.return_value = np.array([1., 1.])
            result = compare_photo(registry, embedder, crop)
            self.assertEqual(result["status"], "unknown")
            self.assertIsNone(result["predicted_name"])
            self.assertEqual(registry.get_pet("cat-a").embedding_count, 1)

    def test_no_features_and_bad_vector_do_not_claim_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            registry = PetRegistry(folder)
            embedder = Mock(model_version="test-v1", backend_name="test")
            embedder.embed.return_value = np.array([1., 0.])
            self.assertEqual(compare_photo(registry, embedder, b"test")["status"], "unknown")
            embedder.embed.return_value = np.array([np.nan, 0.])
            with self.assertRaises(ValueError):
                compare_photo(registry, embedder, b"test")
