"""Tray 只呼叫 localhost API，不持有攝影機、Telegram 或 Xiaomi 秘密。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from typing import Any


class TrayApiError(RuntimeError):
    """Tray 操作失敗；錯誤訊息不得包含秘密。"""


@dataclass(frozen=True)
class TrayApiClient:
    base_url: str = "http://127.0.0.1:8000"
    token: str | None = None
    csrf_token: str | None = None
    timeout_seconds: float = 3.0

    def __post_init__(self) -> None:
        parsed = urllib.parse.urlsplit(self.base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Tray 只允許呼叫本機 Dashboard，不接受 LAN 或外部 URL。")
        if not 0.1 <= self.timeout_seconds <= 30.0:
            raise ValueError("Tray API timeout 必須介於 0.1 到 30 秒。")

    @classmethod
    def from_environment(
        cls,
        *,
        base_url: str = "http://127.0.0.1:8000",
        token_env: str = "MEOWKEEPER_WEB_TOKEN",
    ) -> TrayApiClient:
        return cls(
            base_url=base_url,
            token=os.getenv(token_env),
            csrf_token=(
                hashlib.sha256((os.getenv(token_env, "") + ":csrf").encode("utf-8")).hexdigest()
                if os.getenv(token_env)
                else None
            ),
        )

    @property
    def dashboard_url(self) -> str:
        return self.base_url.rstrip("/") + "/"

    def _request(self, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not path.startswith("/api/v1/"):
            raise TrayApiError("Tray 只允許呼叫版本化本機 API。")
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if method != "GET":
            headers["Content-Type"] = "application/json"
            if self.csrf_token:
                headers["X-MeowKeeper-CSRF"] = self.csrf_token
        request = urllib.request.Request(
            self.base_url.rstrip("/") + path,
            data=json.dumps(payload or {}).encode("utf-8") if method != "GET" else None,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise TrayApiError(f"Dashboard API 回應 HTTP {exc.code}。") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TrayApiError(f"無法連線本機 Dashboard：{type(exc).__name__}。") from None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TrayApiError("Dashboard 回應格式不正確。") from exc

    def health(self) -> dict[str, Any]:
        return self._request("/api/v1/health")

    def wait_for_health(self, *, timeout_seconds: float = 15.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                if self.health().get("status") == "ok":
                    return True
            except TrayApiError:
                time.sleep(0.25)
        return False

    def status(self) -> dict[str, Any]:
        return self._request("/api/v1/status")

    def diagnostics(self) -> dict[str, Any]:
        return self._request("/api/v1/diagnostics")

    def start(self) -> dict[str, Any]:
        return self._request("/api/v1/monitor/start", method="POST")

    def stop(self, *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise TrayApiError("停止背景核心需要二次確認。")
        return self._request("/api/v1/monitor/stop", method="POST", payload={"confirm": True})

    def restart(self, *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise TrayApiError("重新啟動背景核心需要二次確認。")
        return self._request("/api/v1/monitor/restart", method="POST", payload={"confirm": True})

    def shutdown(self) -> dict[str, Any]:
        return self._request("/api/v1/host/shutdown", method="POST", payload={"confirm": True})

    def recent_clip(self, *, pre_seconds: float = 5.0) -> dict[str, Any]:
        return self._request("/api/v1/live/recent", method="POST", payload={"pre_seconds": pre_seconds, "post_seconds": 0.0})

    def tailscale_status(self) -> dict[str, Any]:
        return self._request("/api/v1/tailscale/status")

    def _copy_url(self, value: str, *, allow_tailnet: bool = False) -> bool:
        parsed = urllib.parse.urlsplit(value)
        local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        tailnet = allow_tailnet and parsed.scheme == "https" and parsed.hostname and parsed.hostname.endswith(".ts.net")
        if not local and not tailnet:
            raise TrayApiError("只允許複製已驗證的 localhost Dashboard 網址。")
        if os.name != "nt":
            return False
        try:
            subprocess.run(["clip"], input=value, text=True, check=True, timeout=2.0, shell=False)
            return True
        except (OSError, subprocess.SubprocessError):
            return False

    def copy_dashboard_url(self, url: str | None = None) -> bool:
        return self._copy_url(url or self.dashboard_url)

    def copy_tailscale_url(self) -> bool:
        payload = self.tailscale_status()
        url = payload.get("remote_url") or payload.get("serve_url")
        if not isinstance(url, str) or not url:
            raise TrayApiError("目前沒有可用的 Tailscale Dashboard URL。")
        return self._copy_url(url, allow_tailnet=True)

    def open_dashboard(self) -> bool:
        return bool(webbrowser.open(self.dashboard_url))

    def open_dashboard_page(self, path: str) -> bool:
        """在瀏覽器開啟本機 Dashboard 頁面；不把秘密放入 URL。"""

        if not path.startswith("/") or ".." in path or "?" in path or "#" in path:
            raise TrayApiError("Dashboard 頁面路徑不安全。")
        return bool(webbrowser.open(self.base_url.rstrip("/") + path))

    def open_tailscale_dashboard(self) -> bool:
        payload = self.tailscale_status()
        url = payload.get("remote_url") or payload.get("serve_url")
        if not isinstance(url, str) or not url:
            raise TrayApiError("目前沒有可用的 Tailscale Dashboard URL。")
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".ts.net"):
            raise TrayApiError("Tailscale URL 未通過 HTTPS／Tailnet 網域檢查。")
        return bool(webbrowser.open(url))
