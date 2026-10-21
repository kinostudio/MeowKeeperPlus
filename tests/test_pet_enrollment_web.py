import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from meowkeeper.app.web.main import create_app
from meowkeeper.app.web.session import SessionManager
from meowkeeper.app.web.state import CoreStateFacade
from meowkeeper.core.identity.pet_registry import PetRegistry
from meowkeeper.core.notifications.pet_feedback_wizard import first_round


class _FakeEmbedder:
    model_version = "test-dinov2"

    def __init__(self, *args, **kwargs):
        pass

    def embed(self, _jpeg: bytes) -> np.ndarray:
        return np.asarray([1.0, 0.0], dtype=np.float32)


def _jpeg(seed: int = 0) -> bytes:
    image = Image.new("RGB", (160, 160))
    pixels = []
    for y in range(160):
        for x in range(160):
            value = 220 if (x + y + seed) % 2 else 30
            pixels.append((value, 120 + seed, 70))
    image.putdata(pixels)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=92)
    return output.getvalue()


class PetEnrollmentWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        sessions = SessionManager()
        sessions.configure_admin("tester", "test-only-password")
        facade = CoreStateFacade(event_directory=root / "events")
        self.client = TestClient(create_app(facade=facade, session_manager=sessions))

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def login(self):
        response = self.client.post("/api/v1/auth/login", json={
            "username": "tester", "password": "test-only-password",
        })
        self.assertEqual(response.status_code, 200)
        return {"X-MeowKeeper-CSRF": self.client.cookies.get("meowkeeper_csrf")}

    def test_page_and_api_require_login(self):
        self.assertEqual(self.client.get("/settings/pets", follow_redirects=False).status_code, 307)
        self.assertEqual(self.client.get("/api/v1/pets").status_code, 401)
        self.login()
        page = self.client.get("/settings/pets")
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="pet-enrollment-form"', page.text)
        self.assertIn('id="pet-edit-form"', page.text)
        self.assertIn("/static/js/pets.js", page.text)
        self.assertIn("寵物參考照片", page.text)
        self.assertIn("不需要設定", page.text)
        self.assertIn("攝影機角度或物件位置", page.text)
        self.assertIn("保存照片並建立 AI 參考資料", page.text)
        self.assertNotIn("物件 · 在哪裡", page.text)
        self.assertNotIn("鏡頭方向 · 校正", page.text)
        self.assertNotIn("scene-bind-position", page.text)
        self.assertNotIn("registration-workspace.js", page.text)

    def test_register_photo_and_build_feature_without_blocking_host(self):
        headers = self.login()
        payload = _jpeg()
        files = [
            ("images", ("first.jpg", payload, "image/jpeg")),
            ("images", ("second.jpg", payload.replace(b"\xff\xd8", b"\xff\xd8", 1), "image/jpeg")),
        ]
        with patch("meowkeeper.core.identity.dinov2_embedder.DinoV2Embedder", _FakeEmbedder):
            response = self.client.post(
                "/api/v1/pets",
                data={"display_name": "測試喵", "pet_id": "test_cat", "coat_description": "橘色"},
                files=files,
                headers=headers,
            )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["pet"]["pet_id"], "test_cat")
        self.assertEqual(data["accepted_reference_images"], 1)
        listed = self.client.get("/api/v1/pets").json()["items"]
        self.assertEqual(listed[0]["embedding_status"], "ready")
        registry = PetRegistry(Path(self.temp.name) / "events" / ".." / "pets")
        profile = registry.get_pet("test_cat")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.embedding_count, 1)
        self.assertEqual(listed[0]["display_name"], "測試喵")

    def test_edit_profile_and_add_reference_photo(self):
        headers = self.login()
        with patch("meowkeeper.core.identity.dinov2_embedder.DinoV2Embedder", _FakeEmbedder):
            response = self.client.post(
                "/api/v1/pets",
                data={"display_name": "原名稱", "pet_id": "editable_cat", "coat_description": "橘色"},
                files={"images": ("first.jpg", _jpeg(), "image/jpeg")},
                headers=headers,
            )
            self.assertEqual(response.status_code, 200, response.text)
            edited = self.client.patch(
                "/api/v1/pets/editable_cat",
                json={
                    "display_name": "貓甲",
                    "coat_description": "棕色",
                    "pattern_description": "貍花",
                    "enabled": True,
                },
                headers=headers,
            )
            self.assertEqual(edited.status_code, 200, edited.text)
            added = self.client.post(
                "/api/v1/pets/editable_cat/images",
                files={"images": ("second.jpg", _jpeg(1), "image/jpeg")},
                headers=headers,
            )
        self.assertEqual(added.status_code, 200, added.text)
        profile = PetRegistry(Path(self.temp.name) / "events" / ".." / "pets").get_pet("editable_cat")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.display_name, "貓甲")
        self.assertEqual(profile.pattern_description, "貍花")
        self.assertEqual(len(profile.reference_images), 2)
        self.assertEqual(profile.embedding_count, 2)

    def test_edit_profile_not_found(self):
        headers = self.login()
        response = self.client.patch(
            "/api/v1/pets/missing",
            json={"display_name": "不存在"},
            headers=headers,
        )
        self.assertEqual(response.status_code, 404)

    def test_gallery_replace_delete_and_auth(self):
        headers = self.login()
        with patch("meowkeeper.core.identity.dinov2_embedder.DinoV2Embedder", _FakeEmbedder):
            self.client.post("/api/v1/pets", data={"display_name": "相簿測試", "pet_id": "gallery"},
                             files={"images": ("one.jpg", _jpeg(), "image/jpeg")}, headers=headers)
            pet = self.client.get("/api/v1/pets").json()["items"][0]
            photo = pet["reference_images"][0]
            self.assertEqual(self.client.get(photo["url"]).content, _jpeg())
            self.assertEqual(self.client.delete(photo["url"]).status_code, 403)
            replaced = self.client.put(photo["url"], files={"images": ("new.jpg", _jpeg(2), "image/jpeg")}, headers=headers)
            self.assertEqual(replaced.status_code, 200, replaced.text)
            self.assertEqual(self.client.get(photo["url"]).status_code, 404)
            pet = self.client.get("/api/v1/pets").json()["items"][0]
            self.assertEqual(pet["embedding_count"], 1)
            current = pet["reference_images"][0]["url"]
            self.assertEqual(self.client.get(current).content, _jpeg(2))
            deleted = self.client.delete(current, headers=headers)
            self.assertEqual(deleted.status_code, 200, deleted.text)
            pet = self.client.get("/api/v1/pets").json()["items"][0]
            self.assertEqual(pet["reference_images"], [])
            self.assertEqual(pet["embedding_count"], 0)
            added = self.client.post("/api/v1/pets/gallery/images", files={"images": ("again.jpg", _jpeg(), "image/jpeg")}, headers=headers)
            self.assertEqual(added.status_code, 200, added.text)
        self.client.cookies.clear()
        self.assertEqual(self.client.get(current).status_code, 401)

    def test_telegram_buttons_follow_saved_display_name(self):
        registry = PetRegistry(Path(self.temp.name) / "pets")
        registry.register_pet("舊顯示名稱", pet_id="stable-id")
        before = first_round("event", registry.list_pets())
        self.assertEqual(before["inline_keyboard"][0][0]["text"], "是舊顯示名稱")
        registry.update_profile("stable-id", display_name="新顯示名稱")
        after = first_round("event", registry.list_pets())
        self.assertEqual(after["inline_keyboard"][0][0]["text"], "是新顯示名稱")
        self.assertEqual(before["inline_keyboard"][0][0]["callback_data"], after["inline_keyboard"][0][0]["callback_data"])

    def test_photo_changes_rejected_while_embedding_runs(self):
        headers = self.login()
        registry = PetRegistry(Path(self.temp.name) / "pets")
        registry.register_pet("測試", pet_id="busy")
        image = registry.add_reference_image("busy", _jpeg())
        self.client.app.state.pet_embedding_jobs["busy"] = {"status": "running"}
        response = self.client.delete(f"/api/v1/pets/busy/images/{image.name}", headers=headers)
        self.assertEqual(response.status_code, 409)
        self.assertTrue(image.is_file())

    def test_failed_replacement_does_not_leave_orphan_or_destroy_old_photo(self):
        registry = PetRegistry(Path(self.temp.name) / "pets")
        registry.register_pet("測試", pet_id="rollback")
        image = registry.add_reference_image("rollback", _jpeg())
        original = registry.get_pet("rollback").to_dict()
        with patch.object(registry, "_write_profile", side_effect=PermissionError("test-lock")), \
                self.assertRaises(PermissionError):
            registry.change_reference_image("rollback", image.name, _jpeg(1))
        self.assertEqual(registry.get_pet("rollback").to_dict(), original)
        self.assertEqual(image.read_bytes(), _jpeg())
        self.assertEqual(len(list(image.parent.glob("*.jpg"))), 1)

    def test_invalidated_gallery_cannot_reuse_stale_embeddings(self):
        registry = PetRegistry(Path(self.temp.name) / "pets")
        registry.register_pet("測試", pet_id="reset")
        registry.add_embedding("reset", [1, 0], model_version="test")
        profile = registry.get_pet("reset")
        profile.embedding_count = 0
        registry._write_profile(profile)
        self.assertEqual(registry.get_embeddings("reset"), ([], None))
        registry.add_embedding("reset", [0, 1], model_version="test")
        vectors, _ = registry.get_embeddings("reset")
        self.assertEqual(len(vectors), 1)
        np.testing.assert_array_equal(vectors[0], [0, 1])

    def test_mutation_requires_csrf_and_invalid_photo_is_rejected(self):
        self.login()
        response = self.client.post(
            "/api/v1/pets",
            data={"display_name": "測試喵"},
            files={"images": ("not.jpg", b"not-a-jpeg", "image/jpeg")},
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
