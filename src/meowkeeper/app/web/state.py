"""Web 層使用的核心狀態 façade；不複製媒體或 AI 業務邏輯。"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from meowkeeper.app.web.contracts import public_settings, validate_settings_with_pydantic
from meowkeeper.storage.database import SQLiteStore


class CoreStateFacade:
    """把既有核心的可觀測狀態與操作 callback 暴露給 API。

    未接入真正 service callback 時，生命週期狀態會標示 ``interface_only``，
    不會假裝已啟動 24H 核心。
    """

    def __init__(
        self,
        *,
        store: SQLiteStore | None = None,
        event_directory: str | Path = "data/events",
        settings: Mapping[str, Any] | None = None,
        callbacks: Mapping[str, Callable[..., Any]] | None = None,
        dashboard_network: Any | None = None,
    ) -> None:
        self.store = store
        self.event_directory = Path(event_directory).resolve()
        self._settings = dict(settings or {})
        self._callbacks = dict(callbacks or {})
        self._dashboard_network = dashboard_network
        self._lock = threading.RLock()
        self._started_at: str | None = None
        self._state = "stopped"
        self._capability = "connected" if callbacks and any(
            name in callbacks for name in ("start", "stop", "status")
        ) else "interface_only"
        self._metrics: dict[str, Any] = {
            "source": {"status": "not_checked", "source_id": None, "resolution": None, "fps": None},
            "ffmpeg": {"status": "not_checked", "restart_count": 0},
            "ai": {"backend": "unknown", "device": "unknown", "fps": None, "latency_ms": None},
            "ring_buffer": {"seconds": 0.0, "bytes": 0, "capacity_seconds": 60},
            "telegram": {"status": "disabled", "enabled": False, "success_count": 0, "failure_count": 0},
            "sqlite": {"status": "not_configured", "schema_version": None, "journal_mode": None},
            "snapshot": {"status": "not_checked", "source": None, "width": None, "height": None},
            "tailscale": {"status": "not_checked", "remote_url": None},
            "gpu": {"name": None, "vram_mib": None, "temperature_c": None},
        }
        self.latest_jpeg_path: Path | None = None
        self._resource_last: tuple[float, dict[tuple[int, float], float]] | None = None
        self._resource_cached: tuple[float, dict[str, Any]] | None = None
        self._resource_collecting = False
        self._external_app_pids: set[int] = set()
        self._external_app_scanned_at: float = 0.0

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def update_metrics(self, values: Mapping[str, Any]) -> None:
        with self._lock:
            for key, value in values.items():
                if isinstance(value, Mapping) and isinstance(self._metrics.get(key), dict):
                    self._metrics[key] = {**self._metrics[key], **dict(value)}
                else:
                    self._metrics[key] = value

    def set_latest_jpeg(self, path: str | Path | None) -> None:
        self.latest_jpeg_path = Path(path).resolve() if path else None

    def monitoring_active(self) -> bool:
        """Cheap lifecycle-only check; sensor sampling must not collect full metrics."""
        callback = self._callbacks.get("monitoring_active")
        if callback is not None:
            return callback() is True
        with self._lock:
            return self._state in {"starting", "running"}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            metrics = {key: dict(value) if isinstance(value, dict) else value for key, value in self._metrics.items()}
            state = self._state
            started_at = self._started_at
            capability = self._capability
        status_callback = self._callbacks.get("status")
        if status_callback is not None:
            try:
                live = status_callback()
            except Exception:  # noqa: BLE001 - health endpoint must remain available
                live = None
            if isinstance(live, Mapping):
                if isinstance(live.get("metrics"), Mapping):
                    for key, value in live["metrics"].items():
                        if isinstance(value, Mapping) and isinstance(metrics.get(key), dict):
                            metrics[key] = {**metrics[key], **dict(value)}
                        else:
                            metrics[key] = value
                if isinstance(live.get("status"), str):
                    state = str(live["status"])
                if live.get("started_at"):
                    started_at = str(live["started_at"])
                capability = str(live.get("capability") or "connected")
                if live.get("last_error"):
                    metrics["core"] = {"last_error": str(live["last_error"])}
        if self.store is not None:
            try:
                metrics["sqlite"] = {
                    **metrics.get("sqlite", {}),
                    "status": "ok",
                    "schema_version": self.store.schema_version,
                    "journal_mode": self.store.journal_mode,
                }
            except Exception:  # noqa: BLE001 - health endpoint must remain available
                metrics["sqlite"] = {**metrics.get("sqlite", {}), "status": "error"}
        try:
            import psutil  # type: ignore[import-not-found]

            metrics["system"] = {
                "cpu_percent": psutil.cpu_percent(interval=None),
                "ram_percent": psutil.virtual_memory().percent,
                "process_rss_bytes": psutil.Process(os.getpid()).memory_info().rss,
            }
            metrics["application"] = self._application_resources(psutil)
        except Exception:  # noqa: BLE001 - optional diagnostic only
            metrics["system"] = {"cpu_percent": None, "ram_percent": None, "process_rss_bytes": None}
            metrics["application"] = {"status": "unavailable", "process_count": None, "rss_bytes": None, "cpu_percent": None}
        metrics["disk"] = self._disk_snapshot()
        dashboard = self._dashboard_snapshot()
        return {
            "service": "MeowKeeper+",
            "status": state,
            "lifecycle_capability": capability,
            "started_at": started_at,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics,
            "dashboard": {**dashboard, "remote_url": metrics.get("tailscale", {}).get("remote_url")},
        }

    def _application_resources(self, psutil: Any) -> dict[str, Any]:
        """Return cached accounting immediately; OS process enumeration runs off-thread."""

        now = time.monotonic()
        with self._lock:
            cached = self._resource_cached
            if (cached is None or now - cached[0] >= 5.0) and not self._resource_collecting:
                self._resource_collecting = True
                threading.Thread(target=self._refresh_application_resources, args=(psutil,), daemon=True).start()
        return dict(cached[1]) if cached is not None else {
            "status": "collecting", "process_count": None, "rss_bytes": None, "cpu_percent": None,
            "message_zh_tw": "正在計算喵喵守護者+ 的整體資源用量。",
        }

    def _refresh_application_resources(self, psutil: Any) -> None:
        """One Dashboard total for verified app processes, not one physical PID."""

        try:
            result = self._collect_application_resources(psutil)
        except Exception:  # noqa: BLE001 - diagnostics must never block health
            result = {"status": "unavailable", "process_count": None, "rss_bytes": None, "cpu_percent": None}
        with self._lock:
            self._resource_cached = (time.monotonic(), result)
            self._resource_collecting = False

    def _collect_application_resources(self, psutil: Any) -> dict[str, Any]:
        root = Path(__file__).resolve().parents[4]
        now = time.monotonic()
        pids = {os.getpid()}
        # AI workers may use multiprocessing rather than a run_host.py command line.
        # Descendants belong to this Host even if their Python command is opaque.
        try:
            pids.update(child.pid for child in psutil.Process(os.getpid()).children(recursive=True))
        except (psutil.Error, OSError):
            pass

        # Retain known external processes (e.g. Tray) if still alive
        external_pids = getattr(self, "_external_app_pids", set())
        alive_external: set[int] = set()
        for epid in external_pids:
            try:
                if psutil.pid_exists(epid):
                    alive_external.add(epid)
            except (psutil.Error, OSError):
                pass
        self._external_app_pids = alive_external
        pids.update(self._external_app_pids)

        # Full external process scan runs at most once every 30 seconds to prevent sampling jitter
        last_scanned = getattr(self, "_external_app_scanned_at", 0.0)
        if now - last_scanned >= 30.0:
            self._external_app_scanned_at = now
            root_text = str(root).replace("/", "\\").lower().rstrip("\\") + "\\"
            target_names = {"go2rtc.exe", "ffmpeg.exe", "ffprobe.exe", "python.exe", "pythonw.exe"}
            try:
                for process in psutil.process_iter(attrs=["pid", "name"]):
                    try:
                        p_name = str(process.info.get("name") or "").lower()
                        if p_name not in target_names:
                            continue
                        executable = str(process.exe() or "").replace("/", "\\").lower()
                        if not executable.startswith(root_text):
                            continue
                        name = Path(executable).name
                        arguments = " ".join(str(part) for part in process.cmdline() or ()).replace("/", "\\").lower()
                        if name in {"go2rtc.exe", "ffmpeg.exe", "ffprobe.exe"} or (
                            name in {"python.exe", "pythonw.exe"}
                            and root_text in arguments
                            and ("run_host.py" in arguments or "run_tray.py" in arguments)
                        ):
                            p_pid = int(process.info["pid"])
                            self._external_app_pids.add(p_pid)
                            pids.add(p_pid)
                    except (psutil.Error, OSError):
                        continue
            except (psutil.Error, OSError):
                pass

        rss = 0
        cpu_times: dict[tuple[int, float], float] = {}
        counted = 0
        for pid in pids:
            try:
                process = psutil.Process(pid)
                rss += process.memory_info().rss
                times = process.cpu_times()
                cpu_times[(pid, process.create_time())] = float(times.user + times.system)
                counted += 1
            except (psutil.Error, OSError):
                continue
        with self._lock:
            previous = self._resource_last
            self._resource_last = (now, cpu_times)
        cpu_percent = None
        if previous is not None and now > previous[0]:
            delta = sum(max(0.0, value - previous[1].get(pid, value)) for pid, value in cpu_times.items())
            cpu_percent = round(100.0 * delta / (now - previous[0]) / max(1, psutil.cpu_count() or 1), 1)
        result = {
            "status": "ok", "process_count": counted, "rss_bytes": rss,
            "cpu_percent": cpu_percent,
            "message_zh_tw": "整合 Host、Tray、go2rtc 與內附 FFmpeg 的資源總計；Windows 中仍是多個必要程序。",
        }
        with self._lock:
            self._resource_cached = (now, result)
        return result

    def _dashboard_snapshot(self) -> dict[str, Any]:
        service = self._dashboard_network
        if service is not None and callable(getattr(service, "snapshot", None)):
            try:
                return dict(service.snapshot())
            except (OSError, RuntimeError, ValueError):
                pass
        try:
            port = int(self._settings.get("dashboard_port", 8000))
        except (TypeError, ValueError):
            port = 8000
        enabled = bool(self._settings.get("lan_access_enabled", False))
        return {
            "lan_access_enabled": enabled,
            "bind_host": "0.0.0.0" if enabled else "127.0.0.1",
            "configured_host": "0.0.0.0" if enabled else "127.0.0.1",
            "port": port,
            "configured_port": port,
            "local_url": f"http://127.0.0.1:{port}",
            "urls": [],
            "private_ipv4": [],
            "firewall": {"status": "not_checked", "enabled": False},
            "restart_required": False,
            "message_zh_tw": "Dashboard 網路設定尚未接入專用服務。",
        }

    def _disk_snapshot(self) -> dict[str, Any]:
        try:
            import shutil

            usage = shutil.disk_usage(str(self.event_directory))
            return {"free_bytes": usage.free, "total_bytes": usage.total, "path": str(self.event_directory)}
        except OSError:
            return {"free_bytes": None, "total_bytes": None, "path": str(self.event_directory)}

    def health(self) -> dict[str, Any]:
        state = self.state
        callback = self._callbacks.get("status")
        if callback is not None:
            try:
                current = callback()
                if isinstance(current, Mapping) and isinstance(current.get("status"), str):
                    state = current["status"]
            except Exception:  # noqa: BLE001 - liveness remains separate from core readiness
                state = "unknown"
        return {"status": "ok", "service": "MeowKeeper+", "core_status": state}

    def _lifecycle(self, action: str, *, confirm: bool = False) -> dict[str, Any]:
        if action in {"stop", "restart"} and not confirm:
            raise ValueError("停止或重新啟動背景核心需要 confirm=true。")
        callback = self._callbacks.get(action)
        if callback is not None:
            # The concrete controller requires an explicit confirmation for
            # destructive lifecycle actions.  Keep that confirmation at the
            # Web boundary instead of dropping it before invoking the callback.
            result = callback(confirm=confirm) if action in {"stop", "restart"} else callback()
            with self._lock:
                result_status = result.get("status") if isinstance(result, Mapping) else None
                self._state = str(result_status or ("running" if action in {"start", "restart"} else "stopped"))
                self._capability = "connected"
                if self._state in {"running", "starting"}:
                    self._started_at = self._started_at or datetime.now(timezone.utc).isoformat()
            return {"action": action, "status": self._state, "capability": self._capability, "result": result}
        with self._lock:
            self._state = "running" if action in {"start", "restart"} else "stopped"
            self._capability = "interface_only"
            if self._state == "running":
                self._started_at = datetime.now(timezone.utc).isoformat()
        return {"action": action, "status": self._state, "capability": self._capability}

    def start(self) -> dict[str, Any]:
        return self._lifecycle("start")

    def stop(self, *, confirm: bool = False) -> dict[str, Any]:
        return self._lifecycle("stop", confirm=confirm)

    def restart(self, *, confirm: bool = False) -> dict[str, Any]:
        return self._lifecycle("restart", confirm=confirm)

    def settings(self) -> dict[str, Any]:
        with self._lock:
            return public_settings(self._settings)

    def set_source_summary(self, *, configured: bool, source_type: str) -> None:
        """Keep the public settings summary aligned with a source switch."""

        with self._lock:
            self._settings["source_configured"] = configured
            self._settings["source_type"] = source_type

    def update_settings(self, values: Mapping[str, Any]) -> dict[str, Any]:
        validated = validate_settings_with_pydantic(values)
        network = self._dashboard_network
        if network is not None and ({"lan_access_enabled", "dashboard_port"} & set(validated)):
            current = network.config
            network.update(
                lan_access_enabled=bool(validated.get("lan_access_enabled", current.lan_access_enabled)),
                port=int(validated.get("dashboard_port", current.port)),
            )
        with self._lock:
            self._settings.update(validated)
        callback = self._callbacks.get("settings")
        if callback is not None:
            callback(validated)
        return self.settings()

    def retry_inference(self) -> dict[str, Any]:
        callback = self._callbacks.get("retry_inference")
        if callback is None:
            return {"success": False, "message_zh_tw": "目前版本尚未接上 AI 重試功能。"}
        return callback()

    def list_events(
        self,
        *,
        limit: int,
        offset: int,
        event_type: str | None = None,
        telegram_status: str | None = None,
    ) -> list[dict[str, Any]]:
        if self.store is None:
            return []
        items: list[dict[str, Any]] = []
        root = self.event_directory.resolve()
        for row in self.store.list_media_events(
            limit=limit,
            offset=offset,
            event_type=event_type,
            telegram_status=telegram_status,
        ):
            item = dict(row)
            candidate, clip_location = self._resolve_event_clip(item)
            item["clip_exists"] = candidate is not None
            item["clip_location"] = clip_location
            picture = (root / f"event_{item['event_id']}.jpg").resolve()
            item["snapshot_exists"] = root in picture.parents and picture.is_file()
            item["has_human_review"] = bool(item.get("has_human_review", False))
            item["clip_size_bytes"] = item.get("clip_size_bytes") or (candidate.stat().st_size if candidate else None)
            items.append(item)

        if items and self.store is not None:
            event_ids = [it["event_id"] for it in items]
            placeholders = ",".join("?" for _ in event_ids)
            with self.store._connection_lock:
                inf_rows = self.store.connection.execute(
                    f"""SELECT i.id AS iid, m.event_id, i.note
                        FROM identity_inference_history i
                        JOIN media_events m ON (m.track_id = i.track_id OR m.event_id = i.event_id)
                        WHERE m.event_id IN ({placeholders}) AND i.note LIKE '%identity_evidence_v1%'
                        UNION ALL
                        SELECT i.id AS iid, mt.event_id, i.note
                        FROM identity_inference_history i
                        JOIN media_event_tracks mt ON (mt.track_id = i.track_id)
                        WHERE mt.event_id IN ({placeholders}) AND i.note LIKE '%identity_evidence_v1%'
                        ORDER BY iid DESC""",
                    [*event_ids, *event_ids],
                ).fetchall()
                cloud_rows = self.store.connection.execute(
                    f"SELECT event_id, status FROM event_semantic_candidates WHERE event_id IN ({placeholders})",
                    event_ids,
                ).fetchall()
                beh_rows = self.store.connection.execute(
                    f"""SELECT b.rowid AS rid, m.event_id, b.pet_id, b.behavior_state, b.motion_state
                        FROM behavior_events b
                        JOIN media_events m ON (m.track_id = b.track_id OR m.event_id = b.event_id)
                        WHERE m.event_id IN ({placeholders})
                        UNION ALL
                        SELECT b.rowid AS rid, mt.event_id, b.pet_id, b.behavior_state, b.motion_state
                        FROM behavior_events b
                        JOIN media_event_tracks mt ON (mt.track_id = b.track_id)
                        WHERE mt.event_id IN ({placeholders})
                        ORDER BY rid DESC""",
                    [*event_ids, *event_ids],
                ).fetchall()

            import json
            inf_by_id = {}
            for r in inf_rows:
                eid = r["event_id"]
                if eid in inf_by_id:
                    continue
                try:
                    inf_by_id[eid] = json.loads(r["note"])
                except Exception:
                    pass

            cloud_by_id = {r["event_id"]: r["status"] for r in cloud_rows}
            beh_by_id = {r["event_id"]: dict(r) for r in beh_rows}

            pet_display_names = {}
            try:
                from meowkeeper.core.identity.pet_registry import PetRegistry
                reg = PetRegistry(self.event_directory.parent / "pets")
                pet_display_names = {pet.pet_id: pet.display_name for pet in reg.list_pets()}
            except Exception:
                pass

            for it in items:
                eid = it["event_id"]
                local_data = inf_by_id.get(eid)
                beh_data = beh_by_id.get(eid)
                cloud_status = cloud_by_id.get(eid)

                it["has_local_ai"] = bool(local_data or beh_data)
                it["has_cloud_ai"] = bool(cloud_status == "completed")
                it["cloud_status"] = cloud_status

                # Map candidate pet names
                predicted_names = []
                candidates = []
                posture_zh = None
                if local_data and isinstance(local_data, dict):
                    cands = local_data.get("candidates", [])
                    if isinstance(cands, list):
                        for c in cands:
                            if isinstance(c, dict) and c.get("name"):
                                p_id = c.get("pet_id")
                                name = pet_display_names.get(p_id, c.get("name"))
                                score = float(c.get("pet_score") or c.get("score") or 0.0)
                                candidates.append({"pet_id": p_id, "name": name, "score": round(score, 3)})
                                if score >= 0.65 and name not in predicted_names:
                                    predicted_names.append(name)
                    pred_name = local_data.get("predicted_name")
                    if pred_name and pred_name not in predicted_names and float(local_data.get("similarity") or 0) >= 0.65:
                        predicted_names.insert(0, pred_name)
                    posture_zh = local_data.get("posture", {}).get("label_zh")

                if not predicted_names and it.get("pet_id") and it.get("pet_id") != "unknown":
                    raw_id = it.get("pet_id")
                    predicted_names.append(pet_display_names.get(raw_id, raw_id))

                it["predicted_names"] = predicted_names
                it["candidates"] = candidates
                it["posture_zh"] = posture_zh

        return items

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        if self.store is None:
            return None
        row = self.store.get_media_event(event_id)
        return dict(row) if row is not None else None

    def event_file(self, event_id: str) -> Path | None:
        item = self.get_event(event_id)
        if not item:
            return None
        candidate, _location = self._resolve_event_clip(item)
        return candidate

    def _resolve_event_clip(self, item: Mapping[str, Any]) -> tuple[Path | None, str]:
        """Resolve old absolute event paths after users move data between PCs.

        Event IDs have a strict filename-safe alphabet, so a stale DB path may
        fall back only to this event's canonical name inside the configured
        event directory. Arbitrary paths outside the media root stay blocked.
        """

        root = self.event_directory.resolve()
        stored = item.get("clip_path")
        if stored:
            try:
                candidate = Path(str(stored)).resolve()
                if root in candidate.parents and candidate.is_file():
                    return candidate, "configured_path"
            except (OSError, RuntimeError, ValueError):
                pass
        event_id = str(item.get("event_id") or "")
        if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", event_id):
            relocated = (root / f"event_{event_id}.mp4").resolve()
            if root in relocated.parents and relocated.is_file():
                return relocated, "relocated_by_event_id"
        return None, "missing"

    def request_recent_clip(self, pre_seconds: float, post_seconds: float = 0.0) -> Any:
        callback = self._callbacks.get("recent_clip")
        if callback is None:
            return None
        return callback(pre_seconds, post_seconds)

    def save_capture(self, kind: str) -> dict[str, Any]:
        callback = self._callbacks.get("save_capture")
        if callback is None:
            return {"status": "not_available", "message_zh_tw": "尚未接入本機保存服務。"}
        return dict(callback(kind))

    def simulate_event(self, event_type: str = "cat_detected") -> dict[str, Any]:
        callback = self._callbacks.get("simulate_event")
        if callback is None:
            return {"status": "not_checked", "message_zh_tw": "尚未接入事件模擬服務。"}
        return dict(callback(event_type))

    def request_snapshot(self, *, high_quality: bool = True) -> Path | None:
        """Return an explicit snapshot or the bounded live-preview frame.

        ``high_quality=True`` is used by the explicit snapshot API.  The
        preview page opts out so polling keeps using the existing ingest JPEG
        and never asks go2rtc for a new producer or a full-resolution frame on
        every browser refresh.
        """

        callback = self._callbacks.get("snapshot" if high_quality else "preview_snapshot")
        if callback is None and not high_quality:
            # Preserve compatibility with older controllers that only exposed
            # the original snapshot callback.
            callback = self._callbacks.get("snapshot")
        if callback is not None:
            result = callback()
            return Path(result).resolve() if result else None
        return self.latest_jpeg_path

    def preview_status(self) -> dict[str, Any]:
        """回報既有 ingest JPEG 預覽；此 façade 絕不建立第二條來源連線。"""

        # The live controller owns the ingest preview path.  Ask its existing
        # snapshot callback instead of opening another source connection or
        # keeping a second reader in the Web façade.
        path = self.request_snapshot(high_quality=False)
        source: Mapping[str, Any] = {}
        ai: Mapping[str, Any] = {}
        live_status = self._callbacks.get("status")
        if live_status is not None:
            try:
                live = live_status()
            except Exception:  # noqa: BLE001 - preview metadata cannot stop the Web API
                live = None
            if isinstance(live, Mapping) and isinstance(live.get("metrics"), Mapping):
                candidate = live["metrics"].get("source")
                if isinstance(candidate, Mapping):
                    source = candidate
                candidate_ai = live["metrics"].get("ai")
                if isinstance(candidate_ai, Mapping):
                    ai = candidate_ai
        available = bool(path and path.is_file())
        frame_age = None
        try:
            stamp = datetime.fromisoformat(str(source.get("preview_saved_at") or source.get("last_frame_at")))
            if stamp.tzinfo is not None:
                frame_age = (datetime.now(timezone.utc) - stamp).total_seconds()
        except (TypeError, ValueError, OverflowError):
            pass
        source_id = str(source.get("source_id") or "") or None
        stream_name = str(source.get("stream_name") or "") or None
        source_is_xiaomi = bool(
            source_id and source_id.startswith("Xiaomi")
        ) or bool(stream_name and stream_name.startswith("xiaomi_camera_"))
        return {
            "mode": "existing_ingest_jpeg" if path and path.is_file() else "not_available",
            "source_connections_added": 0,
            "restream_url": None,
            "path_available": available,
            "source_status": source.get("status"),
            "source_connections": source.get("connections"),
            "source_id": source_id,
            "stream_name": stream_name,
            "source_is_xiaomi": source_is_xiaomi,
            "resolution": source.get("resolution"),
            "width": source.get("width"),
            "height": source.get("height"),
            "fps": source.get("fps"),
            "ai_input_size": ai.get("inference_input_size"),
            "ai_analysis_fps": ai.get("fps"),
            "source_epoch": source.get("preview_epoch"),
            "overlay": (ai.get("overlay") if isinstance(ai.get("overlay"), Mapping)
                        and source.get("preview_epoch")
                        and ai["overlay"].get("source_epoch") == source.get("preview_epoch") else None),
            "last_frame_at": source.get("last_frame_at"),
            "preview_saved_at": source.get("preview_saved_at"),
            "frame_age_seconds": frame_age,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "message_zh_tw": (
                "沿用單一 FfmpegMediaIngest 的 JPEG 分支。"
                if available
                else "目前尚未收到單一 FfmpegMediaIngest 的 JPEG 影格。"
            ),
        }

    def diagnostics(self) -> dict[str, Any]:
        return {"generated_at": time.time(), "status": self.snapshot(), "settings": self.settings()}
