import hashlib
import unittest
from unittest.mock import Mock

from fastapi.testclient import TestClient

from meowkeeper.app.web import CoreStateFacade, create_app


class DashboardServiceControlsTests(unittest.TestCase):
    def test_topbar_and_monitor_control_use_existing_full_pipeline(self):
        stop = Mock(return_value={"status": "stopped"})
        facade = CoreStateFacade(callbacks={"stop": stop})
        token = "t" * 32
        with TestClient(create_app(facade=facade, token=token)) as client:
            headers = {"Authorization": "Bearer " + token, "X-MeowKeeper-CSRF": hashlib.sha256((token + ":csrf").encode()).hexdigest()}
            page = client.get("/dashboard", headers=headers).text
            for item in ('id="monitor-toggle"', 'id="host-close"', '更多操作', '停止來源接收'):
                self.assertIn(item, page)
            response = client.post("/api/v1/monitor/stop", headers=headers, json={"confirm": True})
            self.assertEqual(response.status_code, 200)
            stop.assert_called_once_with(confirm=True)
            self.assertEqual(client.get("/api/v1/status", headers=headers).json()["status"], "stopped")

    def test_both_views_mount_same_preview_and_home_actions(self):
        token = "t" * 32
        with TestClient(create_app(token=token)) as client:
            headers = {"Authorization": "Bearer " + token}
            home = client.get("/dashboard", headers=headers).text
            live = client.get("/live", headers=headers).text
            self.assertIn("shared-preview.js", home)
            self.assertIn("shared-preview.js", live)
            for kind in ("photo", "video"):
                self.assertIn(f'data-save-capture="{kind}"', home)
            js = client.get("/static/js/dashboard.js").text
            self.assertIn('fetch("/api/v1/live/save"', js)
            self.assertIn('function renderCards', js)
