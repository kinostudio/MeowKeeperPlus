import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from meowkeeper.app.core_controller import RepositoryCoreController
from meowkeeper.app.web.main import create_app
from meowkeeper.app.web.session import SessionManager
from meowkeeper.storage.database import SQLiteStore


class BehaviorZoneWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = SQLiteStore(root / "events.db")
        self.controller = RepositoryCoreController(
            store=self.store, source_url="rtsp://test:private@camera/first",
            event_directory=root / "events", ring_directory=root / "ring",
            preview_directory=root / "preview", analyzer_mode="v02",
        )
        sessions = SessionManager()
        sessions.configure_admin("tester", "test-only-password")
        self.client = TestClient(create_app(self.controller, session_manager=sessions))
        self.route = "/api/v1/settings/behavior-zones"

    def tearDown(self):
        self.client.close()
        self.controller.close()
        self.store.close()
        self.temp.cleanup()

    def login(self):
        self.assertEqual(self.client.post("/api/v1/auth/login", json={
            "username": "tester", "password": "test-only-password"}).status_code, 200)
        return {"X-MeowKeeper-CSRF": self.client.cookies.get("meowkeeper_csrf")}

    def payload(self):
        data = self.client.get(self.route).json()
        return {"source_ref": data["source_ref"], "reauth": True, "zones": [{
            "zone_id": "food", "name": "飯碗", "zone_type": "food", "enabled": True,
            "polygon": [[0, 0], [0.5, 0], [0.5, 0.5], [0, 0.5]],
        }]}

    def test_auth_csrf_persistence_and_scene_editor_template(self):
        self.assertEqual(self.client.get(self.route).status_code, 401)
        headers = self.login()
        payload = self.payload()
        self.assertEqual(self.client.put(self.route, json=payload).status_code, 403)
        without_confirmation = {**payload, "reauth": False}
        self.assertEqual(self.client.put(self.route, json=without_confirmation, headers=headers).status_code, 428)
        response = self.client.put(self.route, json=payload, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["restart_required"])
        self.assertNotIn("private", response.text)
        self.assertNotIn("rtsp://", response.text)
        self.assertEqual(self.client.get(self.route).json()["zones"][0]["name"], "飯碗")
        self.assertFalse(self.controller.is_running)
        page = self.client.get("/settings/ai")
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="scene-object-canvas"', page.text)
        self.assertIn('id="scene-save-view"', page.text)
        self.assertIn('id="scene-upload-reference"', page.text)
        self.assertIn('id="scene-preview-collage"', page.text)
        self.assertIn("/static/js/scene-registry.js", page.text)
        self.assertNotIn('id="zone-add-form"', page.text)
        self.client.post("/api/v1/auth/logout")
        self.assertEqual(self.client.get(self.route).status_code, 401)

    def test_stale_source_and_invalid_polygon_do_not_replace(self):
        headers = self.login()
        payload = self.payload()
        self.assertEqual(self.client.put(self.route, json=payload, headers=headers).status_code, 200)
        payload["zones"][0]["polygon"][0] = [5, 0]
        self.assertEqual(self.client.put(self.route, json=payload, headers=headers).status_code, 422)
        self.assertEqual(self.client.get(self.route).json()["zones"][0]["polygon"][0], [0, 0])
        self.controller.set_source("different-camera")
        self.assertEqual(self.client.put(self.route, json=payload, headers=headers).status_code, 422)
        self.assertEqual(self.client.get(self.route).json()["zones"], [])

    def test_unconfigured_source_does_not_save(self):
        self.login()
        self.controller.set_source(None)
        response = self.client.get(self.route)
        self.assertEqual(response.status_code, 422)
        self.assertIn("先設定", response.json()["detail"])
