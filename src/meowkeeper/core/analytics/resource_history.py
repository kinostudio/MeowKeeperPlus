"""有界資源歷史；背景採樣，不依賴瀏覽器，也不進入影音主路徑。"""

from __future__ import annotations

import math
import os
import sqlite3
import stat
import threading
import time
from contextlib import contextmanager
from itertools import pairwise
from pathlib import Path
from typing import Any

FIELDS = ("system_cpu_percent", "system_ram_percent", "app_cpu_percent", "app_rss_bytes",
          "app_ram_percent", "process_count", "app_disk_bytes", "data_disk_bytes", "total_disk_bytes",
          "system_gpu_percent", "cpu_temperature_c", "gpu_temperature_c", "disk_read_bytes", "disk_write_bytes")


class ResourceHistory:
    def __init__(self, path: Path, *, retention_days: int = 90) -> None:
        self.path = path
        self.retention_days = retention_days
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in (0, 1, 2):
                raise sqlite3.DatabaseError("資源歷史版本較新，請使用相容版本。")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS resource_samples (sampled_at REAL PRIMARY KEY, "
                       + ", ".join(f"{name} REAL" for name in FIELDS) + ", disk_sampled_at REAL)")
            columns = {row[1] for row in db.execute("PRAGMA table_info(resource_samples)")}
            for name in FIELDS:
                if name not in columns:
                    db.execute(f"ALTER TABLE resource_samples ADD COLUMN {name} REAL")
            db.execute("PRAGMA user_version=2")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=0.5)
        try:
            db.execute("PRAGMA busy_timeout=500")
            with db:
                yield db
        finally:
            db.close()

    def append(self, values: dict[str, Any], *, now: float | None = None) -> None:
        stamp = time.time() if now is None else now
        clean = []
        for name in (*FIELDS, "disk_sampled_at"):
            value = values.get(name)
            clean.append(float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None)
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO resource_samples (sampled_at," + ",".join((*FIELDS, "disk_sampled_at"))
                       + ") VALUES (" + ",".join("?" for _ in range(len(FIELDS) + 2)) + ")",
                       [stamp, *clean])
            db.execute("DELETE FROM resource_samples WHERE sampled_at < ?", (stamp - self.retention_days * 86400,))

    def query(self, hours: int, *, now: float | None = None) -> dict[str, Any]:
        if hours not in (1, 24, 168, 720, 2160):
            raise ValueError("請選擇 1 小時、24 小時、7 天、30 天或 90 天。")
        end = time.time() if now is None else now
        start = end - hours * 3600
        bucket = max(60, math.ceil(hours * 3600 / 600))
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT MIN(sampled_at) AS sampled_at, COUNT(*) AS sample_count, "
                              + ",".join(f"AVG({name}) AS {name}" for name in FIELDS)
                              + ", MAX(disk_sampled_at) AS disk_sampled_at FROM resource_samples "
                              "WHERE sampled_at BETWEEN ? AND ? GROUP BY CAST((sampled_at-?)/? AS INTEGER) "
                              "ORDER BY sampled_at", (start, end, start, bucket)).fetchall()
            latest = db.execute("SELECT * FROM resource_samples ORDER BY sampled_at DESC LIMIT 1").fetchone()
        return {"status": "ok" if rows else "no_data", "points": [dict(row) for row in rows],
                "latest": dict(latest) if latest else None, "bucket_seconds": bucket,
                "retention_days": self.retention_days, "sample_interval_seconds": 60}

    def hour_summary(self, *, now: float | None = None) -> dict:
        end = time.time() if now is None else now
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM resource_samples WHERE sampled_at BETWEEN ? AND ? ORDER BY sampled_at",
                (end - 3600, end))]
        fields = ("system_cpu_percent", "system_ram_percent", "system_gpu_percent", "cpu_temperature_c", "gpu_temperature_c")
        statistics = {}
        for field in fields:
            values = [row[field] for row in rows if row[field] is not None]
            statistics[field] = {"current": rows[-1][field] if rows and end - rows[-1]["sampled_at"] <= 120 else None,
                                 "average": sum(values) / len(values) if values else None,
                                 "peak": max(values) if values else None}
        io = {}
        for field, key in (("disk_read_bytes", "read_bytes"), ("disk_write_bytes", "write_bytes")):
            pairs = [(a[field], b[field]) for a, b in pairwise(rows)
                     if a[field] is not None and b[field] is not None]
            io[key] = sum(b - a for a, b in pairs if b >= a) if pairs else None
        return {"statistics": statistics, "disk_io": io, "sample_count": len(rows),
                "coverage_seconds": rows[-1]["sampled_at"] - rows[0]["sampled_at"] if len(rows) > 1 else 0}


def directory_bytes(root: Path, *, excluded: set[Path], stop: threading.Event) -> int:
    """Logical file bytes; never follow junctions/symlinks or count excluded trees twice."""
    total = 0
    if not root.is_dir():
        raise OSError("資源目錄不存在。")
    def fail(error: OSError) -> None:
        raise error

    for parent, directories, files in os.walk(root, followlinks=False, onerror=fail):
        if stop.is_set():
            raise InterruptedError("磁碟統計已停止。")
        directories[:] = [name for name in directories if not (
            Path(parent, name).resolve() in excluded
            or Path(parent, name).is_symlink()
            or getattr(Path(parent, name).lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        )]
        for name in files:
            file = Path(parent, name)
            if not file.is_symlink():
                try:
                    total += file.stat().st_size
                except FileNotFoundError:
                    pass  # rotating cache files can disappear during enumeration
    return total


class ResourceRecorder:
    def __init__(self, history: ResourceHistory, facade: Any, app_root: Path, data_root: Path, *, cpu_monitor=None) -> None:
        self.history, self.facade = history, facade
        self.app_root, self.data_root = app_root.resolve(), data_root.resolve()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.disk_thread: threading.Thread | None = None
        self.disk: dict[str, Any] = {}
        self.lock = threading.Lock()
        self.last_disk_attempt = -float("inf")
        self.last_error: str | None = None
        self.latest_sample: dict[str, Any] = {}
        self.last_persisted = -float("inf")
        from .cpu_temperature import CpuTemperatureMonitor, InstalledCpuProvider, PsutilCpuProvider
        self.cpu_monitor = cpu_monitor or CpuTemperatureMonitor(
            [PsutilCpuProvider(), InstalledCpuProvider(self.app_root / "vendor/cpu-sensor")],
            active=lambda: self.facade.monitoring_active() is True,
            enabled=os.environ.get("MEOWKEEPER_CPU_TEMPERATURE", "on").lower() not in {"off", "false", "0"})

    def start(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.cpu_monitor.start()
        self.thread = threading.Thread(target=self._run, name="resource-history", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.cpu_monitor.stop()
        for thread in (self.thread, self.disk_thread):
            if thread is not None:
                thread.join(timeout=2)

    def _scan_disk(self) -> None:
        try:
            excluded = {self.data_root, *(self.app_root / name for name in ("dist", ".git", "reports"))}
            app = directory_bytes(self.app_root, excluded=excluded, stop=self.stop_event)
            data = directory_bytes(self.data_root, excluded=set(), stop=self.stop_event)
            with self.lock:
                self.disk = {"app_disk_bytes": app, "data_disk_bytes": data,
                             "total_disk_bytes": app + data, "disk_sampled_at": time.time()}
        except OSError:
            self.last_error = "磁碟占用暫時無法統計；保留最近有效值。"

    def sample(self, *, persist: bool = True) -> None:
        import psutil

        system = psutil.virtual_memory()
        app = self.facade._collect_application_resources(psutil)
        values = {"system_cpu_percent": psutil.cpu_percent(interval=None), "system_ram_percent": system.percent,
                  "ram_used_bytes": system.used, "ram_total_bytes": system.total,
                  "app_cpu_percent": app.get("cpu_percent"), "app_rss_bytes": app.get("rss_bytes"),
                  "app_ram_percent": 100 * app["rss_bytes"] / system.total if app.get("rss_bytes") is not None else None,
                  "process_count": app.get("process_count")}
        from meowkeeper.core.analytics.system_telemetry import gpu_sample
        values.update(gpu_sample())
        values["cpu_temperature_c"] = self.cpu_monitor.snapshot()["temperature_c"]
        io = psutil.disk_io_counters()
        if io is not None:
            values.update(disk_read_bytes=io.read_bytes, disk_write_bytes=io.write_bytes)
        with self.lock:
            values.update(self.disk)
            self.latest_sample = {**values, "sampled_at": time.time(), "gpu_name": values.get("gpu_name"),
                                  "cpu_sensor": self.cpu_monitor.snapshot()}
        if persist:
            self.history.append(values)
            self.last_persisted = time.monotonic()
        now = time.monotonic()
        if now - self.last_disk_attempt >= 900 and (self.disk_thread is None or not self.disk_thread.is_alive()):
            self.last_disk_attempt = now
            self.disk_thread = threading.Thread(target=self._scan_disk, name="resource-disk", daemon=True)
            self.disk_thread.start()

    def _run(self) -> None:
        try:
            import psutil
            psutil.cpu_percent(interval=None)  # prime this thread's system counter
            self.facade._collect_application_resources(psutil)
        except Exception:  # noqa: BLE001 - isolated telemetry must not stop monitoring
            self.last_error = "資源監測套件無法使用。"
        while not self.stop_event.is_set():
            try:
                self.sample(persist=time.monotonic() - self.last_persisted >= 60)
                self.last_error = None
            except Exception:  # noqa: BLE001 - skip one sample, never stop media
                self.last_error = "資源歷史暫時無法保存；影音監控不受影響。"
            self.stop_event.wait(5)

    def live_snapshot(self) -> dict:
        with self.lock:
            return dict(self.latest_sample)
