import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from meowkeeper.app.core_controller import RepositoryCoreController
from meowkeeper.app.web import CoreStateFacade, create_app
from meowkeeper.core.media.sources import FramePacket
from meowkeeper.storage.database import SQLiteStore


class DashboardActivityTests(unittest.TestCase):
    def test_slow_ai_does_not_regress_ingest_timestamp(self):
        now = datetime.now(timezone.utc)
        store = SQLiteStore(":memory:")
        controller = RepositoryCoreController(store=store)
        controller._last_frame_at = now.timestamp()
        ring = SimpleNamespace(status=lambda: SimpleNamespace(duration_seconds=0, size_bytes=0,
                                                               retention_seconds=60, segment_count=0))
        controller._update_runtime_metrics(FramePacket(1, now-timedelta(seconds=30), "mock", b""),
            SimpleNamespace(restart_count=0, last_error=None), ring, SimpleNamespace())
        self.assertEqual(controller.get_status()["metrics"]["source"]["last_frame_at"], now.isoformat())
        controller.close()
        store.close()

    def test_real_events_only_and_confirmed_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "events.db")
            now = datetime.now(timezone.utc)
            for event, kind, offset in [("cat", "cat_detected", 0), ("other", "motion", 0),
                                        ("old", "cat_detected", 8 * 24)]:
                store.create_media_event(event_id=event, event_type=kind, trigger_at=now-timedelta(hours=offset),
                    source_id="test", track_id=None, pet_id=None, confidence=.8,
                    requested_pre_seconds=5, requested_post_seconds=10)
            store.record_telegram_receipt("cat", "a"*64, "video", 123)
            data = store.dashboard_activity(now=now)
            self.assertEqual(len(data["days"]), 7)
            self.assertTrue(all(len(day["hours"]) == 24 for day in data["days"]))
            self.assertEqual(sum(sum(day["hours"]) for day in data["days"]), 1)
            self.assertEqual(sum(day["total"] for day in data["days"]), 1)
            self.assertEqual(data["notifications"]["confirmed"], 1)
            self.assertEqual(data["notifications"]["total"], 2)
            store.close()

    def test_event_list_reads_review_presence_in_the_bounded_page_query(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "events.db")
            store.create_media_event(event_id="event", event_type="cat_detected", trigger_at=datetime.now(timezone.utc),
                source_id="test", track_id=None, pet_id=None, confidence=.8,
                requested_pre_seconds=5, requested_post_seconds=10)
            facade = CoreStateFacade(store=store, event_directory=Path(directory) / "events")
            with patch.object(store, "has_event_review", side_effect=AssertionError("legacy N+1 query")):
                events = facade.list_events(limit=50, offset=0)
            self.assertEqual(len(events), 1)
            self.assertFalse(events[0]["has_human_review"])
            store.close()

    def test_auth_and_empty_database(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "events.db")
            facade = CoreStateFacade(store=store)
            with TestClient(create_app(token="t"*32, facade=facade)) as client:
                self.assertEqual(client.get("/api/v1/analytics/dashboard").status_code, 401)
                response = client.get("/api/v1/analytics/dashboard", headers={"Authorization": "Bearer "+"t"*32})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["notifications"]["total"], 0)
                self.assertNotIn("recipient_ref", response.text)
            store.close()

    def test_ptz_only_on_live_and_versioned_shared_script(self):
        root = Path(__file__).parents[1] / "src/meowkeeper/app/web"
        setup = (root / "templates/xiaomi_setup.html").read_text(encoding="utf-8")
        self.assertNotIn('stateButton.textContent = "讀取方向狀態"', setup)
        self.assertNotIn('move.textContent = `向${label}`', setup)
        base = (root / "templates/base.html").read_text(encoding="utf-8")
        self.assertIn('/static/app.js?v={{ release_info.cache_key }}', base)
        live = (root / "templates/live.html").read_text(encoding="utf-8")
        self.assertIn("PTZ", live)

    def test_relay_prompt_is_generic_and_control_link_not_duplicated(self):
        root = Path(__file__).parents[1] / "src/meowkeeper/app/web"
        setup = (root / "templates/xiaomi_setup.html").read_text(encoding="utf-8")
        self.assertNotIn('if (item.model === "chuangmi.camera.079ae2")', setup)
        self.assertNotIn("方向與隱私控制請到即時畫面", setup)
        self.assertNotIn("Export-C701Pairing.ps1", setup)
        wizard = (root / "static/js/xiaomi-wizard.js").read_text(encoding="utf-8")
        self.assertIn('name="camera-mode"', setup)
        self.assertIn("保存配對不表示目前正在使用中繼", wizard)
        self.assertIn("data-camera-role=\"provider\"", setup)


if __name__ == "__main__":
    unittest.main()
