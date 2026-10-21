from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from meowkeeper.app.web import create_app


class DashboardLiveMetricsTests(unittest.TestCase):
    def test_dashboard_exposes_animated_live_hardware_cards(self):
        token = "m" * 32
        with TestClient(create_app(token=token)) as client:
            page = client.get("/dashboard", headers={"Authorization": "Bearer " + token})
            self.assertEqual(page.status_code, 200)
            for element_id in (
                "hardware-sample-clock", "sample-countdown-ring", "overview-updated",
                "cpu-usage-ring", "overview-ram-ring", "overview-gpu-ring",
                "cpu-temp-needle", "gpu-temp-needle", "cpu-temp-value", "gpu-temp-value",
            ):
                self.assertIn(f'id="{element_id}"', page.text)
            self.assertIn('id="overview-disk-free" class="metric-note" data-rolling-number', page.text)
            self.assertIn("dashboard-live-metrics.js", page.text)
            self.assertIn("dashboard-home-layout.js", page.text)
            self.assertIn('class="overview-grid dashboard-hero-grid"', page.text)
            self.assertIn('class="panel dashboard-preview-panel"', page.text)
            self.assertIn('id="overview-frame" alt="與即時畫面共用的最新來源影格" hidden', page.text)
            self.assertIn('class="panel dashboard-system" aria-label="即時硬體用量"', page.text)
            self.assertIn('class="dashboard-system-tools"', page.text)
            self.assertIn('class="panel dashboard-events-panel"', page.text)

            layout_script = client.get("/static/js/dashboard-home-layout.js")
            self.assertEqual(layout_script.status_code, 200)
            self.assertIn('".dashboard-hero-grid"', layout_script.text)
            self.assertNotIn("fetch(", layout_script.text)

            script = client.get("/static/js/dashboard-live-metrics.js")
            self.assertEqual(script.status_code, 200)
            self.assertIn('"meowkeeper:status"', script.text)
            self.assertIn("prefers-reduced-motion", script.text)
            self.assertIn('"尚未感測"', script.text)
            self.assertNotIn("fetch(", script.text)

            monitor = client.get("/static/js/monitor-ui.js").text
            self.assertIn("const sampleClock=by('hardware-sample-clock')", monitor)
            self.assertIn("hardware.querySelector('.hero-hardware-tools').append(sampleClock)", monitor)
            self.assertNotIn('id="hero-sample-time"', monitor)
