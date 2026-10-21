from __future__ import annotations

import ipaddress
import unittest
from types import SimpleNamespace

from fastapi.testclient import TestClient

from meowkeeper.app.web.api import create_app
from meowkeeper.app.web.network import (
    TAILSCALE_IPV4_NETWORK,
    DashboardNetworkConfig,
    is_dashboard_private_ipv4,
)
from meowkeeper.app.web.state import CoreStateFacade
from tools.run_host import _build_startup_notice, _safe_tailscale_status


class _FakeTailscale:
    executable = "tailscale.exe"

    def __init__(self, status: dict, serve: dict | None = None) -> None:
        self._status = status
        self._serve = serve or {}

    def status(self):
        return SimpleNamespace(to_dict=lambda: dict(self._status))

    def serve_status(self):
        return dict(self._serve)


class LanStartupNoticeTests(unittest.TestCase):
    def test_tailscale_dashboard_endpoint_is_protected_and_read_only(self) -> None:
        token = "a" * 32
        facade = CoreStateFacade(callbacks={
            "tailscale_status": lambda: {
                "status": "ok",
                "logged_in": True,
                "tailscale_ip": "100.64.0.2",
                "remote_dashboard": {"status": "not_configured", "url": None},
            }
        })
        app = create_app(facade=facade, token=token)
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/v1/tailscale/status").status_code, 401)
            response = client.get("/api/v1/tailscale/status", headers={"Authorization": "Bearer " + token})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["tailscale_ip"], "100.64.0.2")
            page = client.get("/settings/tailscale", headers={"Authorization": "Bearer " + token})
            self.assertEqual(page.status_code, 200)
            self.assertIn("檢查 Tailscale", page.text)

    def test_shareable_lan_filter_excludes_tailscale_range(self) -> None:
        self.assertTrue(is_dashboard_private_ipv4("192.168.31.164"))
        self.assertFalse(is_dashboard_private_ipv4("127.0.0.1"))
        self.assertFalse(is_dashboard_private_ipv4("169.254.10.2"))
        self.assertFalse(is_dashboard_private_ipv4("100.64.0.2"))
        self.assertIn(ipaddress.ip_address("100.64.0.2"), TAILSCALE_IPV4_NETWORK)

    def test_tailscale_summary_is_read_only_and_builds_remote_url_only_for_lan(self) -> None:
        client = _FakeTailscale({
            "installed": True,
            "logged_in": True,
            "status": "ok",
            "tailscale_ip": "100.64.0.2",
            "device_name": "meow-pc",
            "magic_dns_name": "meow-pc.example.ts.net",
            "serve_enabled": False,
            "secret_token": "must-not-appear",
        })
        no_lan = _safe_tailscale_status(
            client,
            dashboard_network=SimpleNamespace(config=DashboardNetworkConfig()),
            dashboard_port=8000,
        )
        self.assertEqual(no_lan["remote_dashboard"]["status"], "not_configured")
        self.assertNotIn("secret_token", no_lan)
        lan = _safe_tailscale_status(
            client,
            dashboard_network=SimpleNamespace(
                config=DashboardNetworkConfig(lan_access_enabled=True, host="0.0.0.0")
            ),
            dashboard_port=8123,
        )
        self.assertEqual(lan["remote_dashboard"]["url"], "http://100.64.0.2:8123")

    def test_startup_notice_never_uses_wildcard_or_source_url(self) -> None:
        notice = _build_startup_notice(
            dashboard_snapshot={
                "urls": ["http://192.168.31.164:8000"],
                "local_url": "http://127.0.0.1:8000",
                "bind_host": "0.0.0.0",
            },
            tailscale={"remote_dashboard": {"status": "available", "url": "http://100.64.0.2:8000"}},
            source_type="rtsp",
            source_configured=True,
        )
        self.assertIn("http://192.168.31.164:8000", notice)
        self.assertIn("http://100.64.0.2:8000", notice)
        self.assertNotIn("0.0.0.0", notice)
        self.assertNotIn("rtsp://", notice)
        self.assertNotIn("token", notice.lower())


if __name__ == "__main__":
    unittest.main()
