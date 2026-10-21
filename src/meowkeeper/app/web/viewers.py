"""Short-lived authenticated Dashboard and live-preview activity."""

from __future__ import annotations

import hashlib
import ipaddress
import re
import threading
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

_PAGE_ID = re.compile(r"^[0-9a-fA-F-]{36}$")
_TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
_TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")


def _session_ref(session_id: object) -> str:
    return hashlib.sha256(str(session_id).encode("utf-8", errors="replace")).hexdigest()[:24]


def _safe_client_ip(value: object) -> str | None:
    try:
        address = ipaddress.ip_address(str(value))
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        return str(address)
    except (ValueError, TypeError):
        return None


class LiveViewerTracker:
    """Bounded in-memory activity tracker; no raw session cookie/IP is exposed."""

    def __init__(self, *, dashboard_ttl: float = 60.0, jpeg_ttl: float = 15.0, maximum: int = 256) -> None:
        self.dashboard_ttl = max(10.0, float(dashboard_ttl))
        self.jpeg_ttl = max(5.0, float(jpeg_ttl))
        self.maximum = max(16, int(maximum))
        self._lock = threading.RLock()
        self._dashboard: dict[str, dict[str, Any]] = {}
        self._viewers: dict[str, dict[str, Any]] = {}

    def touch_dashboard(self, session_id: object, client_ip: object, *, now: float | None = None) -> None:
        key = _session_ref(session_id)
        current = time.monotonic() if now is None else float(now)
        with self._lock:
            self._dashboard[key] = {"session_ref": key, "client_ip": _safe_client_ip(client_ip), "last_seen": current}
            self._prune(current)

    def end_session(self, session_id: object) -> None:
        key = _session_ref(session_id)
        with self._lock:
            self._dashboard.pop(key, None)
            for viewer_id, viewer in tuple(self._viewers.items()):
                if viewer.get("session_ref") == key:
                    self._viewers.pop(viewer_id, None)

    def touch_jpeg(self, page_id: object, session_id: object, client_ip: object, *, now: float | None = None) -> bool:
        if not isinstance(page_id, str) or _PAGE_ID.fullmatch(page_id) is None:
            return False
        current = time.monotonic() if now is None else float(now)
        session = _session_ref(session_id)
        key = hashlib.sha256(f"{session}:{page_id}".encode()).hexdigest()[:32]
        with self._lock:
            record = self._viewers.get(key)
            started = record["started"] if record else current
            self._viewers[key] = {
                "session_ref": session,
                "client_ip": _safe_client_ip(client_ip),
                "mode": "jpeg",
                "started": started,
                "last_seen": current,
                "expires_at": current + self.jpeg_ttl,
            }
            self._prune(current)
        return True

    def begin_media(self, connection_id: str, session_id: object, client_ip: object, mode: str, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else float(now)
        with self._lock:
            self._viewers[connection_id] = {
                "session_ref": _session_ref(session_id),
                "client_ip": _safe_client_ip(client_ip),
                "mode": mode if mode in {"mse", "webrtc"} else "unknown",
                "started": current,
                "last_seen": current,
                "expires_at": current + self.dashboard_ttl,
            }
            self._prune(current)

    def touch_media(self, connection_id: str, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else float(now)
        with self._lock:
            record = self._viewers.get(connection_id)
            if record is not None:
                record["last_seen"] = current
                record["expires_at"] = current + self.dashboard_ttl

    def end_media(self, connection_id: str) -> None:
        with self._lock:
            self._viewers.pop(connection_id, None)

    def _prune(self, now: float) -> None:
        for key, item in tuple(self._dashboard.items()):
            if now - item["last_seen"] > self.dashboard_ttl:
                self._dashboard.pop(key, None)
        for key, item in tuple(self._viewers.items()):
            if item.get("expires_at", 0.0) <= now:
                self._viewers.pop(key, None)
        while len(self._dashboard) + len(self._viewers) > self.maximum:
            dashboard_oldest = min(self._dashboard, key=lambda key: self._dashboard[key]["last_seen"]) if self._dashboard else None
            viewer_oldest = min(self._viewers, key=lambda key: self._viewers[key]["last_seen"]) if self._viewers else None
            if viewer_oldest is None or dashboard_oldest is not None and self._dashboard[dashboard_oldest]["last_seen"] <= self._viewers[viewer_oldest]["last_seen"]:
                self._dashboard.pop(dashboard_oldest, None)
            else:
                self._viewers.pop(viewer_oldest, None)

    @staticmethod
    def _device_for_ip(ip: str | None, devices: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
        if not ip:
            return None
        for device in devices:
            ips = device.get("tailnet_ips")
            if isinstance(ips, list) and ip in ips:
                return device
        return None

    @staticmethod
    def _source_kind(ip: str | None) -> str:
        if not ip:
            return "unknown"
        address = ipaddress.ip_address(ip)
        if address.is_loopback:
            return "local"
        if (address.version == 4 and address in _TAILNET_V4) or (address.version == 6 and address in _TAILNET_V6):
            return "tailscale_unmatched"
        if address.is_private or address.is_link_local:
            return "lan_unidentified"
        return "unknown"

    def snapshot(self, devices: list[Mapping[str, Any]] | None = None, aliases: Mapping[str, str] | None = None, *, now: float | None = None) -> dict[str, Any]:
        current = time.monotonic() if now is None else float(now)
        aliases = aliases or {}
        devices = devices or []
        with self._lock:
            self._prune(current)
            dashboard = [dict(value) for value in self._dashboard.values()]
            viewers = [dict(value) for value in self._viewers.values()]

        per_device: dict[str, dict[str, Any]] = {}
        per_dashboard: dict[str, dict[str, Any]] = {}
        unrecognized_live: dict[str, int] = {}
        unrecognized_dashboard: dict[str, int] = {}

        def add_activity(item: Mapping[str, Any], target: dict[str, Any], unknown_counts: dict[str, int], key_name: str) -> None:
            peer = self._device_for_ip(item.get("client_ip"), devices)
            if peer is None:
                kind = self._source_kind(item.get("client_ip"))
                unknown_counts[kind] = unknown_counts.get(kind, 0) + 1
                return
            ref = str(peer.get("device_ref") or "")
            if not ref:
                unknown_counts["unknown"] = unknown_counts.get("unknown", 0) + 1
                return
            entry = target.setdefault(ref, {key_name: 0})
            entry[key_name] += 1

        for item in dashboard:
            add_activity(item, per_dashboard, unrecognized_dashboard, "dashboard_sessions")
        for item in viewers:
            add_activity(item, per_device, unrecognized_live, "live_viewer_connections")

        online_known = [item for item in devices if isinstance(item.get("online"), bool)]
        active_known = [item for item in devices if isinstance(item.get("active"), bool)]
        result_devices: list[dict[str, Any]] = []
        for item in devices:
            ref = str(item.get("device_ref") or "")
            if not ref:
                continue
            live = per_device.get(ref, {})
            dashboard_activity = per_dashboard.get(ref, {})
            dns_name = item.get("dns_name")
            short_dns = str(dns_name).split(".", 1)[0] if dns_name else None
            original_name = short_dns or item.get("hostname") or f"未知裝置（{ref[-4:]}）"
            alias = aliases.get(ref)
            result_devices.append({
                "device_ref": ref,
                "name": alias or original_name,
                "alias": alias,
                "original_name": original_name,
                "os": item.get("os"),
                "online": item.get("online"),
                "active": item.get("active"),
                "last_seen": item.get("last_seen"),
                "is_self": bool(item.get("is_self")),
                "dashboard_sessions": dashboard_activity.get("dashboard_sessions", 0),
                "live_viewer_connections": live.get("live_viewer_connections", 0),
                "is_using_dashboard": bool(dashboard_activity),
                "is_viewing_live": bool(live),
            })
        return {
            "dashboard_session_count": len(dashboard),
            "identified_dashboard_device_count": len(per_dashboard),
            "unidentified_dashboard_sessions": sum(unrecognized_dashboard.values()),
            "live_viewer_connection_count": len(viewers),
            "live_viewer_device_count": len(per_device),
            "unidentified_live_viewer_connections": sum(unrecognized_live.values()),
            "unidentified_viewer_sources": unrecognized_live,
            "live_viewer_modes": {
                mode: sum(item.get("mode") == mode for item in viewers)
                for mode in ("mse", "webrtc", "jpeg")
            },
            "online_device_count": sum(item.get("online") is True for item in devices) if online_known else None,
            "offline_device_count": sum(item.get("online") is False for item in devices) if online_known else None,
            "unknown_online_state_count": len(devices) - len(online_known),
            "active_device_count": sum(item.get("active") is True for item in devices) if active_known else None,
            "unknown_activity_state_count": len(devices) - len(active_known),
            "devices": result_devices,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "activity_window_seconds": int(self.dashboard_ttl),
            "jpeg_expiry_seconds": int(self.jpeg_ttl),
        }
