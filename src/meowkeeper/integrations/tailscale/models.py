"""Tailscale 狀態資料模型；不保存 Auth Key 或 Node Key。"""

from __future__ import annotations

import hashlib
import ipaddress
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class TailscaleStatus:
    installed: bool = False
    version: str | None = None
    logged_in: bool = False
    service_running: bool | None = None
    tailscale_ip: str | None = None
    device_name: str | None = None
    magic_dns_name: str | None = None
    serve_enabled: bool = False
    serve_url: str | None = None
    funnel_enabled: bool = False
    status: str = "not_checked"
    message_zh_tw: str | None = None
    devices: tuple[dict[str, Any], ...] = ()
    device_list_available: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_cli_json(cls, payload: Mapping[str, Any]) -> TailscaleStatus:
        self_node = payload.get("Self") if isinstance(payload.get("Self"), Mapping) else {}
        addresses = self_node.get("TailscaleIPs") or payload.get("TailscaleIPs") or []
        if not isinstance(addresses, list):
            addresses = []
        ip = None
        for value in addresses:
            try:
                address = ipaddress.ip_address(str(value))
            except ValueError:
                continue
            if address.version == 4 and address in ipaddress.ip_network("100.64.0.0/10"):
                ip = str(address)
                break
        dns_name = self_node.get("DNSName") or payload.get("DNSName")
        name = self_node.get("HostName") or payload.get("HostName")
        backend_state = str(payload.get("BackendState", "")).lower()
        normalized_state = backend_state.replace("_", "")
        if normalized_state == "running":
            status, service_running = "ok", True
        elif normalized_state == "starting":
            status, service_running = "starting", None
        elif normalized_state in {"needslogin", "stopped", "nostate"}:
            status = "not_logged_in" if normalized_state == "needslogin" else "stopped" if normalized_state == "stopped" else "no_state"
            service_running = False if normalized_state == "stopped" else None
        else:
            status, service_running = "not_checked", None
        devices: list[dict[str, Any]] = []

        def make_device(raw: Mapping[str, Any], key: str, *, self_device: bool = False) -> dict[str, Any]:
            ip_values = raw.get("TailscaleIPs")
            if not isinstance(ip_values, list):
                ip_values = []
            stable_value = raw.get("ID") or raw.get("NodeID")
            if stable_value is None and self_device and ip_values:
                stable_value = ip_values[0]
            stable_value = stable_value or key
            # Peer map keys contain NodeKey material. Keep only a one-way
            # fingerprint; never return or persist the original key.
            fingerprint = hashlib.sha256(str(stable_value).encode("utf-8", errors="replace")).hexdigest()[:16]
            ips: list[str] = []
            for value in ip_values:
                try:
                    address = ipaddress.ip_address(str(value))
                except ValueError:
                    continue
                if address.version == 4 and address in ipaddress.ip_network("100.64.0.0/10") or address.version == 6 and address in ipaddress.ip_network("fd7a:115c:a1e0::/48"):
                    ips.append(str(address))
            dns = raw.get("DNSName")
            host = raw.get("HostName")
            last_seen = raw.get("LastSeen")
            if not isinstance(last_seen, str) or len(last_seen) > 64:
                last_seen = None
            return {
                "device_ref": f"ts_{fingerprint}",
                "hostname": str(host)[:100] if isinstance(host, str) and host else None,
                "dns_name": str(dns).rstrip(".")[:255] if isinstance(dns, str) and dns else None,
                "os": str(raw.get("OS"))[:40] if isinstance(raw.get("OS"), str) and raw.get("OS") else None,
                "online": bool(raw["Online"]) if isinstance(raw.get("Online"), bool) else (True if self_device and backend_state == "running" else None),
                # Tailscale Active means Tailnet activity, not MeowKeeper use.
                "active": bool(raw["Active"]) if isinstance(raw.get("Active"), bool) else None,
                "last_seen": last_seen,
                "tailnet_ips": ips,
                "is_self": self_device,
            }

        peer_map = payload.get("Peer")
        peer_map_valid = isinstance(peer_map, Mapping)
        if peer_map_valid:
            for key, peer in peer_map.items():
                if isinstance(peer, Mapping):
                    devices.append(make_device(peer, str(key)))
        if isinstance(payload.get("Self"), Mapping):
            devices.append(make_device(self_node, "self", self_device=True))
        return cls(
            installed=True,
            logged_in=normalized_state in {"running", "starting"},
            service_running=service_running,
            tailscale_ip=ip,
            device_name=str(name) if name else None,
            magic_dns_name=str(dns_name).rstrip(".") if dns_name else None,
            status=status,
            devices=tuple(devices),
            device_list_available=peer_map_valid and isinstance(payload.get("Self"), Mapping),
        )
