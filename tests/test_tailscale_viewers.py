from __future__ import annotations

import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from meowkeeper.app.web.api import create_app
from meowkeeper.app.web.session import SessionManager
from meowkeeper.app.web.state import CoreStateFacade
from meowkeeper.app.web.viewers import LiveViewerTracker
from meowkeeper.integrations.tailscale.aliases import TailscaleAliasStore
from meowkeeper.integrations.tailscale.status import parse_status_json
from meowkeeper.integrations.tailscale.status_cache import TailscaleStatusCache
from tools.run_host import _safe_tailscale_status


class TailscaleDeviceParsingTests(unittest.TestCase):
    def test_stable_id_survives_rename_and_raw_node_keys_are_not_returned(self) -> None:
        first = parse_status_json(
            '{"BackendState":"Running","Self":{"ID":"self-1","HostName":"home",'
            '"TailscaleIPs":["100.100.1.1"]},"Peer":{"nodekey:private-1":{"ID":"node-2",'
            '"HostName":"phone-old","DNSName":"phone-old.example.ts.net.","OS":"iOS",'
            '"Online":true,"Active":false,"TailscaleIPs":["100.100.1.2"]}}}'
        )
        renamed = parse_status_json(
            '{"BackendState":"Running","Self":{"ID":"self-1","HostName":"home",'
            '"TailscaleIPs":["100.100.1.1"]},"Peer":{"nodekey:rotated":{"ID":"node-2",'
            '"HostName":"my-phone","DNSName":"my-phone.example.ts.net.","OS":"iOS",'
            '"Online":true,"Active":true,"TailscaleIPs":["100.100.1.2"]}}}'
        )
        self.assertTrue(first.device_list_available)
        first_peer = next(device for device in first.devices if not device["is_self"])
        renamed_peer = next(device for device in renamed.devices if not device["is_self"])
        self.assertEqual(first_peer["device_ref"], renamed_peer["device_ref"])
        self.assertEqual(first_peer["active"], False)
        self.assertNotIn("nodekey:private-1", repr(first.to_dict()))

    def test_missing_peer_map_keeps_device_list_unavailable(self) -> None:
        parsed = parse_status_json('{"BackendState":"Running","Self":{"HostName":"home"}}')
        self.assertFalse(parsed.device_list_available)
        self.assertEqual(len(parsed.devices), 1)  # Self may exist, but is not a complete peer list.
        self.assertTrue(parsed.devices[0]["is_self"])

    def test_partial_cli_status_does_not_claim_an_online_device_count(self) -> None:
        status = parse_status_json('{"BackendState":"Running","Self":{"HostName":"home"}}')

        class Client:
            executable = "tailscale.exe"

            def status(self):
                return status

            def serve_status(self):
                return {}

        sanitized = _safe_tailscale_status(Client(), dashboard_network=None, dashboard_port=8000)
        self.assertFalse(sanitized["device_list_available"])
        self.assertEqual(sanitized["devices"], [])


class LiveViewerTrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = LiveViewerTracker(dashboard_ttl=60, jpeg_ttl=15, maximum=64)
        self.devices = [
            {
                "device_ref": "ts_0123456789abcdef",
                "hostname": "phone",
                "dns_name": "phone.example.ts.net",
                "os": "iOS",
                "online": True,
                "active": False,
                "tailnet_ips": ["100.100.1.2"],
            }
        ]

    def test_sessions_deduplicate_to_device_but_viewing_tabs_remain_separate(self) -> None:
        self.tracker.touch_dashboard("private-session-1", "::ffff:100.100.1.2", now=100)
        self.tracker.touch_dashboard("private-session-1", "100.100.1.2", now=105)
        self.tracker.touch_dashboard("private-session-2", "100.100.1.2", now=106)
        self.tracker.begin_media("mse-tab-1", "private-session-1", "100.100.1.2", "mse", now=107)
        self.tracker.begin_media("mse-tab-2", "private-session-2", "100.100.1.2", "mse", now=108)

        result = self.tracker.snapshot(self.devices, now=109)["devices"][0]
        summary = self.tracker.snapshot(self.devices, now=109)
        self.assertEqual(result["dashboard_sessions"], 2)
        self.assertEqual(result["live_viewer_connections"], 2)
        self.assertTrue(result["is_viewing_live"])
        self.assertEqual(summary["dashboard_session_count"], 2)
        self.assertEqual(summary["identified_dashboard_device_count"], 1)
        self.assertEqual(summary["live_viewer_device_count"], 1)
        self.assertEqual(summary["live_viewer_connection_count"], 2)
        self.assertEqual(summary["live_viewer_modes"]["mse"], 2)
        self.assertNotIn("private-session-1", repr(summary))
        self.assertNotIn("100.100.1.2", repr(summary))

    def test_online_or_active_device_is_not_marked_as_viewing(self) -> None:
        snapshot = self.tracker.snapshot(self.devices, now=100)
        device = snapshot["devices"][0]
        self.assertTrue(device["online"])
        self.assertFalse(device["active"])
        self.assertFalse(device["is_viewing_live"])
        self.assertEqual(snapshot["live_viewer_device_count"], 0)

    def test_proxy_and_unknown_clients_are_not_mapped_by_forwarded_claims(self) -> None:
        self.tracker.touch_dashboard("proxied-session", "127.0.0.1", now=100)
        self.tracker.begin_media("proxied-viewer", "proxied-session", "127.0.0.1", "mse", now=101)
        result = self.tracker.snapshot(self.devices, now=102)
        self.assertEqual(result["identified_dashboard_device_count"], 0)
        self.assertEqual(result["unidentified_dashboard_sessions"], 1)
        self.assertEqual(result["live_viewer_device_count"], 0)
        self.assertEqual(result["unidentified_live_viewer_connections"], 1)
        self.assertEqual(result["unidentified_viewer_sources"], {"local": 1})

    def test_jpeg_activity_expires_and_media_disconnect_removes_immediately(self) -> None:
        self.assertTrue(self.tracker.touch_jpeg("12345678-1234-1234-1234-123456789abc", "s", "100.100.1.2", now=100))
        self.assertEqual(self.tracker.snapshot(self.devices, now=114)["live_viewer_connection_count"], 1)
        self.assertEqual(self.tracker.snapshot(self.devices, now=115)["live_viewer_connection_count"], 0)
        self.tracker.begin_media("mse", "s", "100.100.1.2", "mse", now=200)
        self.tracker.end_media("mse")
        self.assertEqual(self.tracker.snapshot(self.devices, now=201)["live_viewer_connection_count"], 0)

    def test_explicit_logout_removes_all_activity_for_the_session(self) -> None:
        self.tracker.touch_dashboard("logout-session", "100.100.1.2", now=100)
        self.tracker.begin_media("logout-mse", "logout-session", "100.100.1.2", "mse", now=101)
        self.tracker.end_session("logout-session")
        snapshot = self.tracker.snapshot(self.devices, now=102)
        self.assertEqual(snapshot["dashboard_session_count"], 0)
        self.assertEqual(snapshot["live_viewer_connection_count"], 0)


class TailscaleAliasStoreTests(unittest.TestCase):
    def test_alias_persists_and_clear_restores_original(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "aliases.json"
            store = TailscaleAliasStore(path)
            ref = "ts_0123456789abcdef"
            self.assertEqual(store.set_alias(ref, "  家中 手機  ")[ref], "家中 手機")
            reopened = TailscaleAliasStore(path)
            self.assertEqual(reopened.snapshot()[ref], "家中 手機")
            self.assertEqual(reopened.set_alias(ref, None), {})

    def test_alias_write_failure_does_not_create_in_memory_false_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            blocker = Path(directory) / "not-a-directory"
            blocker.write_text("file", encoding="utf-8")
            store = TailscaleAliasStore(blocker / "aliases.json")
            with self.assertRaises(OSError):
                store.set_alias("ts_0123456789abcdef", "手機")
            self.assertEqual(store.snapshot(), {})

    def test_validation_rejects_untrusted_reference_and_long_alias(self) -> None:
        store = TailscaleAliasStore()
        with self.assertRaises(ValueError):
            store.set_alias("raw-node-key", "手機")
        with self.assertRaises(ValueError):
            store.set_alias("ts_0123456789abcdef", "名" * 41)
        with self.assertRaises(TypeError):
            store.set_alias("ts_0123456789abcdef", [])


class TailscaleStatusCacheTests(unittest.TestCase):
    def test_concurrent_dashboard_polls_share_one_cli_read(self) -> None:
        call_lock = threading.Lock()
        calls = 0

        def load_status() -> dict[str, str]:
            nonlocal calls
            with call_lock:
                calls += 1
            time.sleep(0.05)
            return {"status": "ok"}

        cache = TailscaleStatusCache(load_status, ttl_seconds=10)
        with ThreadPoolExecutor(max_workers=8) as executor:
            values = list(executor.map(lambda _index: cache.get(), range(8)))
        self.assertEqual(calls, 1)
        self.assertEqual(values, [{"status": "ok"}] * 8)

    def test_expired_status_is_reloaded_and_callers_receive_copies(self) -> None:
        now = [10.0]
        calls = 0

        def load_status() -> dict[str, str]:
            nonlocal calls
            calls += 1
            return {"status": f"read-{calls}"}

        cache = TailscaleStatusCache(load_status, ttl_seconds=10, clock=lambda: now[0])
        first = cache.get()
        first["status"] = "mutated"
        self.assertEqual(cache.get()["status"], "read-1")
        now[0] += 10
        self.assertEqual(cache.get()["status"], "read-2")
        self.assertEqual(calls, 2)


class TailscaleDeviceApiTests(unittest.TestCase):
    def test_jpeg_viewer_is_counted_only_after_a_successful_frame_and_logout_clears_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "frame.jpg"
            image_path.write_bytes(b"test-jpeg-bytes")
            sessions = SessionManager()
            sessions.configure_admin("preview-admin", "strong-test-password")
            facade = CoreStateFacade()
            facade.request_snapshot = lambda **_kwargs: image_path
            app = create_app(facade=facade, session_manager=sessions)
            with TestClient(app) as client:
                login = client.post("/api/v1/auth/login", json={
                    "username": "preview-admin", "password": "strong-test-password",
                })
                self.assertEqual(login.status_code, 200)
                response = client.get("/api/v1/preview/jpeg", headers={
                    "X-MeowKeeper-Viewer-Id": "12345678-1234-1234-1234-123456789abc",
                })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b"test-jpeg-bytes")
                tracker = app.state.live_viewer_tracker
                self.assertEqual(tracker.snapshot()["live_viewer_connection_count"], 1)
                logout = client.post("/api/v1/auth/logout")
                self.assertEqual(logout.status_code, 200)
                self.assertEqual(tracker.snapshot()["live_viewer_connection_count"], 0)
                self.assertEqual(tracker.snapshot()["dashboard_session_count"], 0)

    def test_failed_jpeg_fetch_does_not_count_as_watching(self) -> None:
        sessions = SessionManager()
        sessions.configure_admin("preview-admin", "strong-test-password")
        facade = CoreStateFacade()
        facade.request_snapshot = lambda **_kwargs: None
        app = create_app(facade=facade, session_manager=sessions)
        with TestClient(app) as client:
            login = client.post("/api/v1/auth/login", json={
                "username": "preview-admin", "password": "strong-test-password",
            })
            self.assertEqual(login.status_code, 200)
            response = client.get("/api/v1/preview/jpeg", headers={
                "X-MeowKeeper-Viewer-Id": "12345678-1234-1234-1234-123456789abc",
            })
            self.assertEqual(response.status_code, 404)
            self.assertEqual(app.state.live_viewer_tracker.snapshot()["live_viewer_connection_count"], 0)

    def test_details_and_aliases_require_auth_and_csrf_and_omit_addresses(self) -> None:
        token = "a" * 32
        device = {
            "device_ref": "ts_0123456789abcdef",
            "hostname": "phone",
            "dns_name": "phone.example.ts.net",
            "os": "iOS",
            "online": True,
            "active": False,
            "tailnet_ips": ["100.100.1.2"],
            "is_self": False,
        }
        facade = CoreStateFacade(callbacks={"tailscale_status": lambda: {
            "status": "ok", "logged_in": True, "device_list_available": True,
            "checked_at": "2026-10-17T00:00:00+00:00", "devices": [device],
        }})
        app = create_app(facade=facade, token=token)
        csrf = app.state.authenticator.csrf_token
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/v1/tailscale/devices").status_code, 401)
            headers = {"Authorization": f"Bearer {token}"}
            status = client.get("/api/v1/tailscale/status", headers=headers)
            self.assertEqual(status.status_code, 200)
            self.assertNotIn("devices", status.json())
            self.assertNotIn("100.100.1.2", status.text)
            details = client.get("/api/v1/tailscale/devices", headers=headers)
            self.assertEqual(details.status_code, 200)
            self.assertNotIn("100.100.1.2", details.text)
            self.assertEqual(details.json()["summary"]["online_device_count"], 1)
            self.assertEqual(details.json()["summary"]["devices"][0]["name"], "phone")
            alias_url = "/api/v1/tailscale/devices/ts_0123456789abcdef/alias"
            self.assertEqual(client.put(alias_url, headers=headers, json={"alias": "家中手機"}).status_code, 403)
            self.assertEqual(client.put(alias_url, headers={**headers, "X-MeowKeeper-CSRF": csrf}, json={"alias": "家中手機"}).status_code, 200)
            self.assertEqual(client.put(alias_url, headers={**headers, "X-MeowKeeper-CSRF": csrf}, json={}).status_code, 400)
            self.assertEqual(client.put(alias_url, headers={**headers, "X-MeowKeeper-CSRF": csrf}, json={"alias": []}).status_code, 422)
            self.assertEqual(client.put("/api/v1/tailscale/devices/ts_ffffffffffffffff/alias", headers={**headers, "X-MeowKeeper-CSRF": csrf}, json={"alias": "假裝存在"}).status_code, 404)
            self.assertEqual(client.get("/api/v1/tailscale/devices", headers=headers).json()["summary"]["devices"][0]["name"], "家中手機")


if __name__ == "__main__":
    unittest.main()
