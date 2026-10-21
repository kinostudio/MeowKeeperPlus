from __future__ import annotations

import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from meowkeeper.app.web.api import create_app
from meowkeeper.app.web.network import (
    DashboardNetworkConfig,
    DashboardNetworkService,
    WindowsFirewallManager,
    load_dashboard_config,
    resolve_dashboard_config,
    save_dashboard_config,
)
from meowkeeper.app.web.session import SessionManager


class Phase3ELanNetworkTests(unittest.TestCase):
    def test_lan_candidates_are_discovered_without_claiming_loopback_is_shareable(self) -> None:
        service = DashboardNetworkService(DashboardNetworkConfig(port=8123), firewall=Mock())
        with patch("meowkeeper.app.web.network.private_ipv4_addresses", return_value=[
            {"interface": "Wi-Fi", "address": "192.168.31.150"},
        ]):
            snapshot = service.snapshot()
        self.assertEqual(snapshot["urls"], [])
        self.assertEqual(snapshot["lan_candidate_urls"], ["http://192.168.31.150:8123"])
        self.assertEqual(snapshot["bind_host"], "127.0.0.1")

    def test_default_and_explicit_lan_config_are_safe(self) -> None:
        self.assertEqual(load_dashboard_config(), DashboardNetworkConfig())
        lan = DashboardNetworkConfig(lan_access_enabled=True, host="127.0.0.1", port=8123)
        self.assertEqual(lan.bind_host, "0.0.0.0")
        with self.assertRaises(ValueError):
            DashboardNetworkConfig(lan_access_enabled=False, host="192.168.1.2", port=8123)

    def test_config_write_preserves_unrelated_fields_and_is_restart_scoped(self) -> None:
        with tempfile.TemporaryDirectory(prefix="meowkeeper-phase3e-") as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"schema_version": 3, "telegram": {"secret_id": "keep"}}), encoding="utf-8")
            config = DashboardNetworkConfig(lan_access_enabled=True, host="0.0.0.0", port=8123)
            save_dashboard_config(path, config)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["telegram"]["secret_id"], "keep")
            self.assertEqual(load_dashboard_config(path).port, 8123)
            service = DashboardNetworkService(DashboardNetworkConfig(), config_path=path)
            snapshot = service.update(lan_access_enabled=True, port=8123)
            self.assertTrue(snapshot["restart_required"])
            self.assertEqual(snapshot["configured_host"], "0.0.0.0")

    def test_explicit_no_lan_access_resets_persisted_wildcard_host(self) -> None:
        with tempfile.TemporaryDirectory(prefix="meowkeeper-phase3e-") as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "dashboard": {
                            "lan_access_enabled": True,
                            "host": "0.0.0.0",
                            "port": 8123,
                        }
                    }
                ),
                encoding="utf-8",
            )
            resolved = resolve_dashboard_config(path, lan_access_enabled=False)
            self.assertFalse(resolved.lan_access_enabled)
            self.assertEqual(resolved.host, "127.0.0.1")
            self.assertEqual(resolved.port, 8123)

    def test_firewall_command_is_private_dashboard_port_only(self) -> None:
        calls: list[list[str]] = []

        def runner(command: list[str], **kwargs: object):
            calls.append(command)
            if "show" in command:
                return __import__("subprocess").CompletedProcess(
                    command,
                    0,
                    "Rule Name: MeowKeeper+ Dashboard LAN\nEnabled: Yes\nProfiles: Private\nLocalPort: 8123\n",
                    "",
                )
            return __import__("subprocess").CompletedProcess(command, 0, "", "")

        manager = WindowsFirewallManager(runner=runner)
        with patch("meowkeeper.app.web.network.os.name", "nt"):
            result = manager.enable(8123)
        self.assertEqual(result["status"], "enabled")
        self.assertTrue(any("profile=Private" in argument for argument in calls[0]))
        self.assertTrue(any("localport=8123" in argument for argument in calls[0]))
        self.assertFalse(any("1984" in argument or "8554" in argument for argument in calls[0]))

    def test_private_ip_listing_excludes_loopback_and_link_local(self) -> None:
        from meowkeeper.app.web.network import private_ipv4_addresses

        for item in private_ipv4_addresses():
            self.assertNotIn(item["address"], {"127.0.0.1", "169.254.1.1"})

    def test_network_routes_and_protected_media(self) -> None:
        token = "e" * 32
        csrf = hashlib.sha256((token + ":csrf").encode()).hexdigest()
        app = create_app(token=token)
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/v1/network/dashboard").status_code, 401)
            headers = {"Authorization": "Bearer " + token, "X-MeowKeeper-CSRF": csrf}
            self.assertEqual(client.get("/api/v1/network/dashboard", headers=headers).status_code, 200)
            updated = client.put(
                "/api/v1/network/dashboard",
                json={"lan_access_enabled": True, "dashboard_port": 8123},
                headers=headers,
            )
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["network"]["configured_host"], "0.0.0.0")
            self.assertEqual(client.get("/api/v1/preview/jpeg").status_code, 401)
            self.assertEqual(client.get("/api/v1/events/000/video").status_code, 401)
            self.assertEqual(client.get("/api/v1/diagnostics").status_code, 401)

    def test_session_cookie_is_http_only_path_root_and_http_compatible(self) -> None:
        sessions = SessionManager()
        sessions.configure_admin("lan-admin", "a-strong-password-123")
        app = create_app(session_manager=sessions)
        with TestClient(app) as client:
            response = client.post("/api/v1/auth/login", json={"username": "lan-admin", "password": "a-strong-password-123"})
        cookie = response.headers.get("set-cookie", "").lower()
        self.assertIn("httponly", cookie)
        self.assertIn("path=/", cookie)
        self.assertIn("samesite=strict", cookie)
        self.assertNotIn("domain=localhost", cookie)
        self.assertNotIn("secure", cookie)

    def test_frontend_uses_same_origin_paths(self) -> None:
        root = Path(__file__).resolve().parents[1] / "src" / "meowkeeper" / "app" / "web"
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in list((root / "templates").glob("*.html")) + list((root / "static").rglob("*.js"))
        )
        self.assertIsNone(re.search(r"(?:https?|wss?|ws)://(?:127\\.0\\.0\\.1|localhost)", source, re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()
