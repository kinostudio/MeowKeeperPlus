"""Dashboard 區網 bind、私有 IPv4 與 Windows 防火牆安全邊界。

這個模組只處理 Dashboard 的非秘密網路設定。go2rtc API/RTSP 仍由既有
localhost bridge 管理，這裡不會修改或代理任何 Xiaomi 串流設定。
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_DASHBOARD_HOST = "127.0.0.1"
DEFAULT_DASHBOARD_PORT = 8000
FIREWALL_RULE_NAME = "MeowKeeper+ Dashboard LAN"
PRIVATE_PROFILE = "Private"
TAILSCALE_IPV4_NETWORK = ipaddress.ip_network("100.64.0.0/10")

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_VIRTUAL_INTERFACE_MARKERS = (
    "virtual",
    "vmware",
    "hyper-v",
    "vethernet",
    "wsl",
    "docker",
    "tailscale",
    "loopback",
    "teredo",
    "wireguard",
    "hamachi",
    "vpn",
)


@dataclass(frozen=True)
class DashboardNetworkConfig:
    """非秘密 Dashboard bind 設定。

    ``host`` 是設定檔中的顯示值；真正綁定位址由 ``bind_host`` 推導，
    只有明確啟用 LAN 時才會使用 wildcard bind。
    """

    lan_access_enabled: bool = False
    host: str = DEFAULT_DASHBOARD_HOST
    port: int = DEFAULT_DASHBOARD_PORT

    def __post_init__(self) -> None:
        port = int(self.port)
        if not 1 <= port <= 65535:
            raise ValueError("Dashboard Port 必須介於 1 到 65535。")
        host = str(self.host).strip().lower()
        if self.lan_access_enabled:
            # LAN mode always binds all local interfaces.  A user-supplied
            # public address is never accepted as an alternate bind target.
            host = "0.0.0.0"
        elif host not in _LOCAL_HOSTS:
            raise ValueError("未啟用區網存取時 Dashboard 只能綁定 localhost。")
        object.__setattr__(self, "port", port)
        object.__setattr__(self, "host", host)

    @property
    def bind_host(self) -> str:
        return "0.0.0.0" if self.lan_access_enabled else DEFAULT_DASHBOARD_HOST

    @property
    def health_host(self) -> str:
        """Host 本機健康檢查永遠使用 loopback，不使用 wildcard URL。"""

        return DEFAULT_DASHBOARD_HOST

    def to_dict(self) -> dict[str, Any]:
        return {
            "lan_access_enabled": self.lan_access_enabled,
            "host": self.bind_host,
            "port": self.port,
        }


def _section_from_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    dashboard = payload.get("dashboard")
    if isinstance(dashboard, Mapping):
        return dashboard
    # ``web`` is the historical config.example.json section.  It is read-only
    # compatibility input; writes always use the new dashboard section.
    web = payload.get("web")
    return web if isinstance(web, Mapping) else {}


def load_dashboard_config(path: str | Path | None = None) -> DashboardNetworkConfig:
    """讀取非秘密 Dashboard 設定；檔案不存在時使用安全的 localhost 預設。"""

    if path is None:
        return DashboardNetworkConfig()
    config_path = Path(path).resolve()
    if not config_path.is_file():
        return DashboardNetworkConfig()
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"Dashboard 設定檔無法讀取：{config_path}") from exc
    if not isinstance(payload, Mapping):
        raise TypeError("Dashboard 設定檔必須是 JSON object。")
    section = _section_from_payload(payload)
    host = section.get("host", DEFAULT_DASHBOARD_HOST)
    port = section.get("port", DEFAULT_DASHBOARD_PORT)
    enabled = section.get("lan_access_enabled", False)
    if isinstance(enabled, bool) is False:
        raise ValueError("dashboard.lan_access_enabled 必須是布林值。")
    try:
        port_value = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError("dashboard.port 必須是整數。") from exc
    return DashboardNetworkConfig(lan_access_enabled=enabled, host=str(host), port=port_value)


def resolve_dashboard_config(
    path: str | Path | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
    lan_access_enabled: bool | None = None,
) -> DashboardNetworkConfig:
    """套用 CLI 覆寫；沒有覆寫時由 config.json 決定。"""

    current = load_dashboard_config(path)
    enabled = current.lan_access_enabled if lan_access_enabled is None else bool(lan_access_enabled)
    if host is None and lan_access_enabled is not None and not enabled:
        # Explicit ``--no-lan-access`` must also undo a persisted wildcard
        # host.  Otherwise DashboardNetworkConfig correctly rejects the
        # unsafe combination ``enabled=false, host=0.0.0.0``.
        selected_host = DEFAULT_DASHBOARD_HOST
    else:
        selected_host = current.host if host is None else str(host).strip()
    selected_port = current.port if port is None else int(port)
    return DashboardNetworkConfig(lan_access_enabled=enabled, host=selected_host, port=selected_port)


def save_dashboard_config(path: str | Path, config: DashboardNetworkConfig) -> None:
    """保留 config.json 其他欄位，只原子更新非秘密 dashboard 區段。"""

    config_path = Path(path).resolve()
    payload: dict[str, Any] = {}
    if config_path.is_file():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError(f"無法保留現有設定：{config_path}") from exc
        if not isinstance(loaded, dict):
            raise ValueError("設定檔必須是 JSON object。")
        payload = dict(loaded)
    payload["dashboard"] = config.to_dict()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = config_path.with_suffix(config_path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(config_path)


def _is_virtual_interface(name: str) -> bool:
    lowered = name.strip().lower()
    return any(marker in lowered for marker in _VIRTUAL_INTERFACE_MARKERS)


def is_dashboard_private_ipv4(address: str | ipaddress._BaseAddress) -> bool:
    """Return whether an address is suitable for a shareable LAN URL."""

    try:
        parsed = ipaddress.ip_address(str(address))
    except ValueError:
        return False
    return bool(
        parsed.version == 4
        and parsed.is_private
        and not parsed.is_loopback
        and not parsed.is_link_local
        and not parsed.is_unspecified
        and not parsed.is_multicast
        and parsed not in TAILSCALE_IPV4_NETWORK
    )


def private_ipv4_addresses() -> list[dict[str, str]]:
    """列出目前啟用實體介面的私有 IPv4；每次呼叫都重新計算。"""

    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        return []
    result: list[dict[str, str]] = []
    try:
        addresses = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
    except OSError:
        return []
    for interface, values in addresses.items():
        if _is_virtual_interface(interface):
            continue
        state = stats.get(interface)
        if state is not None and not state.isup:
            continue
        for item in values:
            if item.family != socket.AF_INET:
                continue
            try:
                address = ipaddress.ip_address(str(item.address))
            except ValueError:
                continue
            if not is_dashboard_private_ipv4(address):
                continue
            result.append({"interface": interface, "address": str(address)})
    return sorted(result, key=lambda item: (item["interface"].lower(), item["address"]))


def listener_bind_matches(port: int, bind_host: str) -> bool | None:
    """檢查目前 TCP listener 是否符合 Dashboard 期望 bind。

    ``None`` 代表 psutil 不可用或無法讀取，呼叫端應採保守的既有程序重用策略，
    不因診斷工具不可用而啟動第二個 Host。
    """

    try:
        import psutil  # type: ignore[import-not-found]

        connections = psutil.net_connections(kind="tcp")
    except (ImportError, OSError):
        return None
    addresses: list[str] = []
    for connection in connections:
        if connection.status != getattr(psutil, "CONN_LISTEN", "LISTEN") or not connection.laddr:
            continue
        if int(getattr(connection.laddr, "port", 0)) != int(port):
            continue
        addresses.append(str(getattr(connection.laddr, "ip", "")))
    if not addresses:
        return False
    if bind_host in {"0.0.0.0", "::"}:
        return any(address in {"0.0.0.0", "::"} for address in addresses)
    expected = str(bind_host).lower()
    return any(address.lower() == expected for address in addresses)


class WindowsFirewallManager:
    """只管理固定名稱的 Dashboard TCP Private rule。"""

    def __init__(self, *, runner: Callable[..., subprocess.CompletedProcess[str]] | None = None) -> None:
        self._runner = runner or subprocess.run

    @staticmethod
    def _validate_port(port: int) -> int:
        value = int(port)
        if not 1 <= value <= 65535:
            raise ValueError("防火牆 Port 必須介於 1 到 65535。")
        return value

    def _run(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return self._runner(
            ["netsh", "advfirewall", "firewall", *arguments],
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )

    @staticmethod
    def _permission_denied(output: str) -> bool:
        text = output.lower()
        return "access is denied" in text or "拒絕存取" in text or "需要提升" in text

    def status(self, port: int) -> dict[str, Any]:
        value = self._validate_port(port)
        base = {"rule_name": FIREWALL_RULE_NAME, "profile": PRIVATE_PROFILE, "port": value}
        if os.name != "nt":
            return {**base, "status": "not_supported", "enabled": False, "message_zh_tw": "目前不是 Windows，未執行防火牆查詢。"}
        try:
            completed = self._run(["show", "rule", f"name={FIREWALL_RULE_NAME}"])
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {**base, "status": "not_checked", "enabled": False, "message_zh_tw": f"防火牆狀態尚未確認：{type(exc).__name__}"}
        output = (completed.stdout or "") + (completed.stderr or "")
        if completed.returncode != 0:
            return {
                **base,
                "status": "permission_denied" if self._permission_denied(output) else "not_configured",
                "enabled": False,
                "message_zh_tw": "尚未建立 Private Dashboard 防火牆規則。",
            }
        # A same-named rule with Public/Any profile must not be reported as safe.
        profile_ok = bool(re.search(r"profiles?\s*:\s*private\b", output, re.IGNORECASE)) and not bool(
            re.search(r"profiles?\s*:\s*(?:any|all)\b", output, re.IGNORECASE)
        )
        port_ok = bool(re.search(rf"localport\s*:\s*.*\b{value}\b", output, re.IGNORECASE))
        enabled = bool(re.search(r"enabled\s*:\s*yes\b", output, re.IGNORECASE))
        if profile_ok and port_ok and enabled:
            return {**base, "status": "enabled", "enabled": True, "message_zh_tw": "已啟用 Windows Private Profile 防火牆規則。"}
        return {**base, "status": "not_verified", "enabled": False, "message_zh_tw": "找到同名規則，但未確認為僅限 Private Dashboard Port。"}

    def enable(self, port: int) -> dict[str, Any]:
        value = self._validate_port(port)
        if os.name != "nt":
            return self.status(value)
        try:
            completed = self._run([
                "add", "rule", f"name={FIREWALL_RULE_NAME}", "dir=in", "action=allow",
                "protocol=TCP", f"localport={value}", f"profile={PRIVATE_PROFILE}", "enable=yes",
            ])
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"status": "not_checked", "enabled": False, "port": value, "message_zh_tw": f"防火牆規則未變更：{type(exc).__name__}"}
        if completed.returncode != 0:
            output = (completed.stdout or "") + (completed.stderr or "")
            return {"status": "permission_denied" if self._permission_denied(output) else "error", "enabled": False, "port": value, "message_zh_tw": "無法建立防火牆規則；請以系統管理員權限操作。"}
        return self.status(value)

    def disable(self, port: int) -> dict[str, Any]:
        value = self._validate_port(port)
        if os.name != "nt":
            return self.status(value)
        try:
            completed = self._run(["delete", "rule", f"name={FIREWALL_RULE_NAME}"])
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"status": "not_checked", "enabled": False, "port": value, "message_zh_tw": f"防火牆規則未變更：{type(exc).__name__}"}
        if completed.returncode != 0:
            output = (completed.stdout or "") + (completed.stderr or "")
            return {"status": "permission_denied" if self._permission_denied(output) else "error", "enabled": False, "port": value, "message_zh_tw": "無法移除防火牆規則；請以系統管理員權限操作。"}
        return self.status(value)


class DashboardNetworkService:
    """提供 API／Tray 共用的動態 URL 與重啟後生效設定。"""

    def __init__(
        self,
        config: DashboardNetworkConfig,
        *,
        config_path: str | Path | None = None,
        firewall: WindowsFirewallManager | None = None,
    ) -> None:
        self._config = config
        self._running_config = config
        self.config_path = Path(config_path).resolve() if config_path else None
        self.firewall = firewall or WindowsFirewallManager()

    @property
    def config(self) -> DashboardNetworkConfig:
        return self._config

    def snapshot(self) -> dict[str, Any]:
        addresses = private_ipv4_addresses()
        urls = [f"http://{item['address']}:{self._config.port}" for item in addresses]
        local_url = f"http://{DEFAULT_DASHBOARD_HOST}:{self._config.port}"
        return {
            "lan_access_enabled": self._config.lan_access_enabled,
            "bind_host": self._running_config.bind_host,
            "configured_host": self._config.bind_host,
            "port": self._running_config.port,
            "configured_port": self._config.port,
            "local_url": local_url,
            "urls": urls if self._config.lan_access_enabled else [],
            "lan_candidate_urls": [f"http://{item['address']}:{self._running_config.port}" for item in addresses],
            "private_ipv4": addresses,
            "firewall": self.firewall.status(self._config.port),
            "restart_required": (
                self._config.bind_host != self._running_config.bind_host
                or self._config.port != self._running_config.port
            ),
            "message_zh_tw": (
                "設定已儲存；請重新啟動 Dashboard 才會套用新的監聽位址或 Port。"
                if self._config.bind_host != self._running_config.bind_host or self._config.port != self._running_config.port
                else "Dashboard 目前使用單一設定的監聽位址。"
            ),
        }

    def update(self, *, lan_access_enabled: bool, port: int) -> dict[str, Any]:
        config = DashboardNetworkConfig(
            lan_access_enabled=bool(lan_access_enabled),
            host="0.0.0.0" if lan_access_enabled else DEFAULT_DASHBOARD_HOST,
            port=int(port),
        )
        if self.config_path is not None:
            save_dashboard_config(self.config_path, config)
        self._config = config
        return self.snapshot()

    def firewall_action(self, action: str) -> dict[str, Any]:
        if action == "enable":
            return self.firewall.enable(self._config.port)
        if action == "disable":
            return self.firewall.disable(self._config.port)
        raise ValueError("不支援的防火牆操作。")


__all__ = [
    "DEFAULT_DASHBOARD_HOST",
    "DEFAULT_DASHBOARD_PORT",
    "FIREWALL_RULE_NAME",
    "TAILSCALE_IPV4_NETWORK",
    "DashboardNetworkConfig",
    "DashboardNetworkService",
    "WindowsFirewallManager",
    "is_dashboard_private_ipv4",
    "listener_bind_matches",
    "load_dashboard_config",
    "private_ipv4_addresses",
    "resolve_dashboard_config",
    "save_dashboard_config",
]
