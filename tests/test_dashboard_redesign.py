from __future__ import annotations

import json
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient

from meowkeeper.app.web import create_app
from meowkeeper.app.web.release_info import release_information


class _Ids(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name == "id":
                self.ids.append(value)


class DashboardRedesignTests(unittest.TestCase):
    def test_release_version_is_from_manifest_not_hardcoded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(release_information(root)["version"], "開發版")
            (root / "build_manifest.json").write_text(json.dumps({"package_type": "windows_portable",
                "output": "C:/build/MeowKeeperPlus-portable-2026-10-12-r123"}), encoding="utf-8")
            release = release_information(root)
            self.assertEqual(release["version"], "r123")
            self.assertEqual(release["date"], "2026-10-12")
            self.assertNotIn("C:/", str(release))

    def test_home_preserves_controls_unique_ids_and_local_assets(self):
        with TestClient(create_app(token="t" * 32)) as client:
            response = client.get("/dashboard", headers={"Authorization": "Bearer " + "t" * 32})
            self.assertEqual(response.status_code, 200)
            parser = _Ids(); parser.feed(response.text)
            self.assertEqual(len(parser.ids), len(set(parser.ids)))
            for node in ("overview-frame", "overview-resource-charts", "system-status", "host-restart",
                         "full-system-status", "release-version", "events-list", "primary-nav"):
                self.assertIn(node, parser.ids)
            self.assertIn("/static/css/redesign.css", response.text)
            self.assertNotIn("cdn.tailwindcss", response.text)
            self.assertNotIn("fonts.googleapis", response.text)
            for glyph in ("◎", "▣", "▷", "▧", "▤", "▥", "▱", "◷", "⛶"):
                self.assertNotIn(glyph, response.text)
            self.assertIn('class="heading-icon"', response.text)
            self.assertIn('viewBox="0 0 24 24"', response.text)
            self.assertIn("不是整台電腦的上下傳速率", response.text)

    def test_dashboard_service_cards_keep_details_and_preview_aspect_ratio(self):
        token = "t" * 32
        with TestClient(create_app(token=token)) as client:
            page = client.get("/dashboard", headers={"Authorization": "Bearer " + token})
            self.assertEqual(page.status_code, 200)
            for node_id in (
                "overview-bitrate", "overview-frame-age", "overview-frame-time",
                "overview-ai-fps", "overview-ai-model", "overview-identity",
                "overview-recipient-count", "overview-last-delivery", "overview-queue",
            ):
                self.assertIn(f'id="{node_id}"', page.text)
            styles = client.get("/static/css/monitor.css")
            self.assertEqual(styles.status_code, 200)
            self.assertIn(".monitor-hero .hero-services .status-rows", styles.text)
            self.assertIn(".monitor-hero .hero-services .field-help", styles.text)
            self.assertIn(".monitor-hero .hero-services .card-footnote", styles.text)
            self.assertIn(".monitor-hero>.dashboard-preview-panel .overview-preview", styles.text)
            self.assertIn("aspect-ratio:16/9;max-height:none", styles.text)
            self.assertIn("grid-template-columns:minmax(0,2fr) minmax(0,3fr)", styles.text)

    def test_full_status_uses_exact_service_snapshot_and_requires_auth(self):
        service = Mock()
        snapshot = {"hardware": {"hostname": "test-host"}, "telemetry": {"sample_count": 0}}
        service.snapshot.return_value = snapshot
        with TestClient(create_app(token="t" * 32, status_service=service)) as client:
            self.assertEqual(client.get("/api/v1/system/status").status_code, 401)
            service.snapshot.assert_not_called()
            response = client.get("/api/v1/system/status", headers={"Authorization": "Bearer " + "t" * 32})
            self.assertEqual(response.json(), snapshot)
            service.snapshot.assert_called_once_with()

    def test_home_resource_charts_use_real_cpu_ram_gpu_and_temperature_history(self):
        token = "t" * 32
        with TestClient(create_app(token=token)) as client:
            page = client.get("/dashboard", headers={"Authorization": "Bearer " + token})
            self.assertEqual(page.status_code, 200)
            self.assertIn('class="help-drawer" open', page.text)
            self.assertIn("CPU／RAM／GPU 使用率與溫度｜最近 24 小時", page.text)
            self.assertIn("/static/js/dashboard-resource-charts.js", page.text)
            script = client.get("/static/js/dashboard-resource-charts.js")
            self.assertEqual(script.status_code, 200)
            styles = client.get("/static/css/redesign.css")
            self.assertEqual(styles.status_code, 200)
            self.assertIn("dashboard-chart-tooltip", styles.text)
            for metric in ("system_cpu_percent", "system_ram_percent", "system_gpu_percent",
                           "cpu_temperature_c", "gpu_temperature_c"):
                self.assertIn(metric, script.text)
            self.assertIn("pointermove", script.text)
            self.assertIn("ArrowLeft", script.text)
            self.assertIn("無感測資料", script.text)
