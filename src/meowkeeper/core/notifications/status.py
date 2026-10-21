"""Single runtime status snapshot shared by Telegram commands and startup notices."""

from __future__ import annotations

import ipaddress
import os
import shutil
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit


class RuntimeStatusService:
    """Collect truthful, secret-free Host health from live runtime providers."""

    def __init__(
        self,
        *,
        controller: Any,
        store: Any,
        dashboard_network: Any | None = None,
        go2rtc_status: Callable[[], Any] | None = None,
        tailscale_status: Callable[[], Mapping[str, Any]] | None = None,
        dashboard_health: Callable[[], bool] | None = None,
        telegram_enabled: bool = False,
        telegram_queue_pending: Callable[[], int] | None = None,
        source_type: str = "unknown",
        source_configured: bool = False,
        privacy_adapter_configured: bool = False,
        privacy_status: Callable[[], Mapping[str, Any]] | None = None,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.controller = controller
        self.store = store
        self.dashboard_network = dashboard_network
        self.go2rtc_status = go2rtc_status
        self.tailscale_status = tailscale_status
        self.dashboard_health = dashboard_health
        self.telegram_enabled = bool(telegram_enabled)
        self.telegram_queue_pending = telegram_queue_pending or (lambda: 0)
        self.source_type = str(source_type or "unknown")[:24]
        self.source_configured = bool(source_configured)
        self.privacy_adapter_configured = bool(privacy_adapter_configured)
        self.privacy_status = privacy_status
        self.logger = logger or (lambda _message: None)
        self.resource_history = None

    def snapshot(self) -> dict[str, Any]:
        """Read current providers once and return a safe, serializable snapshot."""

        try:
            raw = self.controller.get_status()
        except Exception as exc:  # noqa: BLE001 - status must fail closed
            self._log(f"⚠️ Telegram 狀態讀取失敗：{type(exc).__name__}。")
            raw = {}
        raw = raw if isinstance(raw, Mapping) else {}
        metrics = raw.get("metrics", {})
        metrics = metrics if isinstance(metrics, Mapping) else {}
        source = self._mapping(metrics.get("source"))
        ai = self._mapping(metrics.get("ai"))
        ring = self._mapping(metrics.get("ring_buffer"))
        telegram = self._mapping(metrics.get("telegram"))

        state = str(raw.get("status") or "unknown")[:32]
        started_at = raw.get("started_at")
        dashboard_ok = self._dashboard_is_healthy()
        frame_at = source.get("last_frame_at")
        frame_recent = self._is_recent(frame_at, 20.0)
        source_status = str(source.get("status") or "unknown")[:24]
        camera_connected = source_status in {"running", "connected", "streaming"} and frame_recent
        dashboard = self._dashboard_snapshot()
        tailscale_url = self._tailscale_url()
        bridge = self._go2rtc_snapshot()
        cpu, ram = self._system_usage()
        disk = self._disk_free()
        try:
            pending = max(0, int(self.telegram_queue_pending()))
        except Exception:  # noqa: BLE001 - queue telemetry is optional
            pending = 0

        privacy = {"verified": False, "privacy_mode": None}
        if callable(self.privacy_status):
            try:
                result = self.privacy_status()
                if result.get("verified") is True and type(result.get("privacy_mode")) is bool:
                    privacy = {"verified": True, "privacy_mode": result["privacy_mode"]}
            except Exception:  # noqa: BLE001 - do not leak camera errors
                self.logger("攝影機隱私狀態讀回失敗；狀態尚未確認。")
        try:
            from meowkeeper.core.analytics.system_telemetry import hardware
            hardware_data = hardware()
            telemetry = self.resource_history.hour_summary() if self.resource_history else {}
        except Exception:  # noqa: BLE001 - missing sensors must not stop status
            hardware_data, telemetry = {}, {}
        return {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "hardware": hardware_data,
            "telemetry": telemetry,
            "host": {
                "healthy": dashboard_ok,
                "state": state,
                "pid": os.getpid(),
                "started_at": started_at,
                "uptime": self._uptime(started_at),
            },
            "dashboard": {
                "healthy": dashboard_ok,
                "urls": dashboard["urls"],
                "lan_candidate_urls": dashboard["lan_candidate_urls"],
                "lan_access_enabled": dashboard["lan_access_enabled"],
                "tailscale_url": tailscale_url,
            },
            "camera": {
                "connected": camera_connected,
                "source_status": source_status,
                "source_type": self.source_type,
                "configured": self.source_configured,
                "last_frame_at": frame_at,
                "resolution": str(source.get("resolution") or "unknown")[:32],
                "fps": source.get("fps") if isinstance(source.get("fps"), (int, float)) else None,
                "connections": source.get("connections") if isinstance(source.get("connections"), int) else None,
            },
            "ai": {
                "available": str(ai.get("device") or ai.get("backend") or "unknown")
                not in {"not_started", "unavailable", "unknown"},
                "device": str(ai.get("device") or ai.get("backend") or "unknown")[:40],
                "model": str(ai.get("model") or "unknown")[:48],
            },
            "ring_buffer_seconds": self._nonnegative_float(ring.get("seconds")),
            "telegram": {
                "enabled": self.telegram_enabled or bool(telegram.get("enabled")),
                "queue_pending": pending,
            },
            "system": {"cpu": cpu, "ram": ram},
            "go2rtc": bridge,
            "sqlite": {
                "journal_mode": str(getattr(self.store, "journal_mode", "unknown")).lower()[:16],
                "schema_version": getattr(self.store, "schema_version", None),
            },
            "storage_free": disk,
            "privacy_adapter_configured": self.privacy_adapter_configured,
            "privacy": privacy,
        }

    def command_text(self) -> str:
        snapshot = self.snapshot()
        host = snapshot["host"]
        dashboard = snapshot["dashboard"]
        camera = snapshot["camera"]
        ai = snapshot["ai"]
        telegram = snapshot["telegram"]
        go2rtc = snapshot["go2rtc"]
        sqlite = snapshot["sqlite"]
        urls = dashboard["urls"]
        if dashboard["tailscale_url"]:
            urls = [*urls, dashboard["tailscale_url"]]
        dashboard_detail = "、".join(urls) if urls else "尚未啟用區網／遠端存取"
        candidates = dashboard["lan_candidate_urls"]
        if not dashboard["lan_access_enabled"] and candidates:
            dashboard_detail += "｜內網候選：" + "、".join(candidates) + "（僅本機監聽，需啟用區網存取）"
        if not dashboard["tailscale_url"]:
            dashboard_detail += "｜Tailscale：尚無已驗證的 Dashboard 網址"
        camera_state = "🟢 20 秒內有新影格" if camera["connected"] else f"⚪ 來源未連線或無近期影格（來源 {camera['source_status']}）"
        resolution = camera["resolution"] if camera["resolution"] != "unknown" else "未取得"
        fps = camera["fps"] if camera["fps"] is not None else "未提供"
        source_detail = f"；來源連線 {camera['connections']}" if camera["connections"] is not None else ""
        state = host["state"]
        host_state = "🟢 執行中" if host["healthy"] and state in {"running", "starting", "waiting_for_source"} else f"⚪ {state}"
        cpu = snapshot["system"]["cpu"]
        ram = snapshot["system"]["ram"]
        disk_free = snapshot["storage_free"]
        disk_text = f"可用 {disk_free / (10 ** 9):.1f} GB" if disk_free is not None else "未提供"
        bridge_text = "🟢 API 與 RTSP loopback 可用" if go2rtc["healthy"] else "⚪ 未連線／狀態不可用"
        bridge_details = [str(value) for value in (go2rtc.get("pid"), go2rtc.get("version")) if value]
        if go2rtc["healthy"] and bridge_details:
            bridge_text += f"（{'｜'.join(bridge_details)}）"
        journal = sqlite["journal_mode"]
        db_status = "正常" if journal == "wal" else "可用"
        return "\n".join((
            "📊 喵喵守護者+ 即時狀態",
            f"Host：{host_state}｜PID {host['pid']}｜執行 {host['uptime']}",
            "", "🌐 儀表板", f"狀態：{'🟢 正常' if dashboard['healthy'] else '🔴 無法使用'}",
            "網址：\n" + dashboard_detail.replace("、", "\n"), "",
            "📷 攝影機與 AI", f"畫面：{camera_state}", f"解析度：{resolution}｜FPS：{fps}{source_detail}",
            f"AI：{'🟢 可用' if ai['available'] else '⚪ 未就緒'}｜{ai['model']}｜{ai['device']}",
            "", self._hardware_text(cpu, ram, info=snapshot["hardware"], history=snapshot["telemetry"]), "",
            "📨 通知與儲存",
            f"環形快取：{snapshot['ring_buffer_seconds']:.1f} 秒",
            f"Telegram：{'送出啟用' if telegram['enabled'] else '未啟用'}｜命令接收與送出佇列 {telegram['queue_pending']}",
            f"go2rtc：{bridge_text}",
            f"SQLite：{db_status}｜schema {sqlite['schema_version'] if sqlite['schema_version'] is not None else '未知'}",
            f"儲存空間：{disk_text}",
            ("Privacy：隱私位置已確認" if snapshot["privacy"]["privacy_mode"] else "Privacy：正常位置已確認")
            if snapshot["privacy"]["verified"] else
            "Privacy：Adapter 已設定，狀態尚未確認" if snapshot["privacy_adapter_configured"] else "Privacy：未設定已驗證相機隱私位置",
        ))

    def _hardware_text(self, cpu: str, ram: str, *, info: dict | None = None, history: dict | None = None) -> str:
        from meowkeeper.core.analytics.system_telemetry import format_system_report
        try:
            return f"即時系統：CPU {cpu}｜RAM {ram}\n" + format_system_report(
                history if history is not None else (self.resource_history.hour_summary() if self.resource_history else {}),
                info=info)
        except Exception:  # noqa: BLE001 - status must not interrupt media
            return f"系統：CPU {cpu}｜RAM {ram}；硬體歷史暫時無法取得。"

    def startup_notice(self) -> str:
        snapshot = self.snapshot()
        host = snapshot["host"]
        dashboard = snapshot["dashboard"]
        camera = snapshot["camera"]
        ai = snapshot["ai"]
        telegram = snapshot["telegram"]
        go2rtc = snapshot["go2rtc"]
        urls = dashboard["urls"]
        if dashboard["tailscale_url"]:
            urls = [*urls, dashboard["tailscale_url"]]
        dashboard_state = "🟢 Healthy" if dashboard["healthy"] else "🔴 Unavailable"
        camera_state = "🟢 20 秒內有新影格" if camera["connected"] else f"⚪ 來源未連線或無近期影格（{camera['source_status']}）"
        ai_state = f"🟢 {ai['model']}／{ai['device']}" if ai["available"] else "⚪ 尚未就緒"
        go2rtc_state = "🟢 API 與 RTSP loopback 可用" if go2rtc["healthy"] else "⚪ 未連線／狀態不可用"
        return "\n".join((
            "喵喵守護者+ 啟動狀態",
            f"Host：{'🟢' if host['healthy'] else '🔴'} PID {host['pid']}｜{host['uptime']}",
            f"Dashboard：{dashboard_state}",
            f"LAN／遠端 Dashboard：{'、'.join(urls) if urls else 'Unavailable'}",
            "內網候選：" + ("、".join(dashboard["lan_candidate_urls"]) or "未取得")
            + ("（僅本機監聽，需啟用區網存取）" if not dashboard["lan_access_enabled"] else ""),
            f"Camera：{camera_state}",
            f"來源：{camera['source_type'] if camera['configured'] else 'Disabled'}",
            f"AI：{ai_state}",
            f"系統：CPU {snapshot['system']['cpu']}｜RAM {snapshot['system']['ram']}",
            f"go2rtc：{go2rtc_state}",
            f"Telegram：{'Enabled' if telegram['enabled'] else 'Disabled'}",
        ))

    def _dashboard_is_healthy(self) -> bool:
        if not callable(self.dashboard_health):
            return False
        try:
            return bool(self.dashboard_health())
        except Exception as exc:  # noqa: BLE001 - a broken health probe is unavailable
            self._log(f"⚠️ Dashboard 健康檢查失敗：{type(exc).__name__}。")
            return False

    def _dashboard_snapshot(self) -> dict[str, Any]:
        urls: list[str] = []
        candidates: list[str] = []
        lan_enabled = False
        try:
            result = self.dashboard_network.snapshot() if self.dashboard_network is not None else {}
            if isinstance(result, Mapping):
                lan_enabled = bool(result.get("lan_access_enabled"))
                if result.get("bind_host") in {"127.0.0.1", "localhost", "::1"}:
                    lan_enabled = False
                candidate_values = result.get("lan_candidate_urls", [])
                if isinstance(candidate_values, (list, tuple)):
                    for raw in candidate_values:
                        safe = self._safe_lan_url(raw)
                        if safe and safe not in candidates:
                            candidates.append(safe)
                values = result.get("urls", [])
                if lan_enabled and isinstance(values, (list, tuple)):
                    for raw in values:
                        safe = self._safe_lan_url(raw)
                        if safe and safe not in urls:
                            urls.append(safe)
        except Exception as exc:  # noqa: BLE001 - IP enumeration cannot break status
            self._log(f"⚠️ Telegram Dashboard 網址暫不可用：{type(exc).__name__}。")
        return {"urls": urls, "lan_access_enabled": lan_enabled, "lan_candidate_urls": candidates}

    def _tailscale_url(self) -> str | None:
        if not callable(self.tailscale_status):
            return None
        try:
            result = self.tailscale_status()
            remote = result.get("remote_dashboard") if isinstance(result, Mapping) else None
            raw = remote.get("url") if isinstance(remote, Mapping) else None
            return self._safe_remote_url(raw)
        except Exception as exc:  # noqa: BLE001 - remote diagnostics are optional
            self._log(f"⚠️ Telegram Tailscale 狀態暫不可用：{type(exc).__name__}。")
            return None

    def _go2rtc_snapshot(self) -> dict[str, Any]:
        try:
            result = self.go2rtc_status() if callable(self.go2rtc_status) else None
            return {
                "healthy": bool(getattr(result, "healthy", False)),
                "pid": getattr(result, "pid", None),
                "version": str(getattr(result, "version", "") or "")[:32] or None,
            }
        except Exception as exc:  # noqa: BLE001 - broken bridge check must not break status
            self._log(f"⚠️ Telegram go2rtc 狀態檢查失敗：{type(exc).__name__}。")
            return {"healthy": False, "pid": None, "version": None}

    @staticmethod
    def _system_usage() -> tuple[str, str]:
        try:
            import psutil  # type: ignore[import-not-found]

            return f"{psutil.cpu_percent(interval=0.1):.0f}%", f"{psutil.virtual_memory().percent:.0f}%"
        except Exception:  # noqa: BLE001 - system telemetry is optional
            return "未提供", "未提供"

    def _disk_free(self) -> int | None:
        try:
            path = getattr(self.store, "path", None)
            target = path.parent if path is not None else None
            return int(shutil.disk_usage(target).free) if target is not None else None
        except (OSError, TypeError, AttributeError):
            return None

    @staticmethod
    def _mapping(value: Any) -> Mapping[str, Any]:
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _nonnegative_float(value: Any) -> float:
        try:
            return max(0.0, float(value or 0.0))
        except (TypeError, ValueError, OverflowError):
            return 0.0

    @classmethod
    def _is_recent(cls, value: Any, max_age: float) -> bool:
        try:
            parsed = datetime.fromisoformat(str(value))
            if parsed.tzinfo is None:
                return False
            age = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
            return 0 <= age <= max_age
        except (TypeError, ValueError, OverflowError):
            return False

    @classmethod
    def _uptime(cls, started_at: Any) -> str:
        try:
            parsed = datetime.fromisoformat(str(started_at))
            elapsed = max(0, int((datetime.now().astimezone() - parsed.astimezone()).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            return "未知"
        hours, remainder = divmod(elapsed, 3600)
        minutes, _ = divmod(remainder, 60)
        return f"{hours} 小時 {minutes} 分鐘"

    @staticmethod
    def _safe_lan_url(raw: Any) -> str | None:
        try:
            parts = urlsplit(str(raw))
            address = ipaddress.ip_address(parts.hostname or "")
            if (parts.scheme != "http" or not address.is_private or address.is_loopback
                    or address.is_link_local or address.is_unspecified or address.is_reserved):
                return None
            if parts.username or parts.password or parts.query or parts.fragment or parts.port is None:
                return None
            return str(raw)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_remote_url(raw: Any) -> str | None:
        try:
            parts = urlsplit(str(raw))
            if (parts.scheme not in {"http", "https"} or not parts.hostname
                    or parts.username or parts.password or parts.query or parts.fragment):
                return None
            host = parts.hostname.lower()
            if host.endswith(".ts.net"):
                return str(raw)
            address = ipaddress.ip_address(host)
            if address in ipaddress.ip_network("100.64.0.0/10"):
                return str(raw)
            return None
        except (ValueError, TypeError):
            return None

    def _log(self, message: str) -> None:
        try:
            self.logger(message)
        except Exception:  # noqa: BLE001 - logging must never break a status response
            self.logger = lambda _message: None


__all__ = ["RuntimeStatusService"]
