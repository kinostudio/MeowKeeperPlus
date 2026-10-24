"""單一Host的背景CPU感測器；無提權、網路或GPU操作。"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import queue
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from .cpu_sensor_contract import parse_sensor_time, select_temperature


@lru_cache(maxsize=1)
def cpu_name():
    name = platform.processor() or None
    if platform.system() == "Windows":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                name = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    return name


def unavailable(status="unavailable", reason="sensor_missing", provider=None):
    return {"device_type": "cpu", "temperature_c": None, "sensor_type": None,
            "sensor_name": None, "hardware_name": None, "provider": provider,
            "observed_at": None, "status": status, "error_reason": reason, "confidence": "unavailable"}


class PsutilCpuProvider:
    def read(self):
        import psutil
        get = getattr(psutil, "sensors_temperatures", None)
        if not callable(get):
            return unavailable()
        rows, vendor = [], None
        for group, sensors in get().items():
            if group.lower() not in {"coretemp", "k10temp"}:
                continue
            vendor = "intel" if group.lower() == "coretemp" else "amd"
            for sensor in sensors:
                label = str(sensor.label)
                name = ("CPU Package" if label.startswith("Package id ") else
                        "CPU Core #" + label[5:] if re.fullmatch(r"Core \d+", label) else
                        "Core (Tdie)" if label == "Tdie" else label)
                rows.append({"hardware_name": group, "sensor_name": name,
                             "sensor_id": f"{group}/{label}", "temperature_c": sensor.current})
        value = select_temperature({"schema_version": 1, "provider": "libre_hardware_monitor",
                                    "status": "available", "observed_at": datetime.now(timezone.utc).isoformat(),
                                    "sensors": rows}, vendor=vendor or "unknown")
        value["provider"] = "psutil_cpu"
        return value

    def close(self):
        pass


class HelperCpuProvider:
    """One owned child, bounded stdout, nonce per response; never invokes RunAs."""
    def __init__(self, folder: Path):
        self.folder = folder
        self.lock = threading.Lock()
        self.process = None
        self.reader = None
        self.lines = queue.Queue(maxsize=1)
        self.protocol_error = threading.Event()

    @property
    def alive(self):
        return self.process is not None and self.process.poll() is None

    def _validate(self):
        manifest_path = self.folder / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError()
        if manifest_path.stat().st_size > 32768:
            raise ValueError()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = manifest["files"]
        if manifest.get("schema_version") != 1 or not isinstance(files, dict) or not 2 <= len(files) <= 64:
            raise ValueError()
        if "MeowKeeperCpuSensor.exe" not in files or "LibreHardwareMonitorLib.dll" not in files:
            raise ValueError()
        for name, digest in files.items():
            if (Path(name).name != name or not (name.endswith(".dll") or name == "MeowKeeperCpuSensor.exe")
                    or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)):
                raise ValueError()
            path = self.folder / name
            if (path.is_symlink() or path.resolve().parent != self.folder.resolve()
                    or path.stat().st_size > 10_000_000 or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
                raise ValueError()
        if {p.name for p in self.folder.glob("*.dll")} != {name for name in files if name.endswith(".dll")}:
            raise ValueError()

    def _drain(self, process, lines, failed):
        try:
            while True:
                data = process.stdout.readline(65537)
                if not data:
                    failed.set()
                    return
                if len(data) > 65536:
                    failed.set()
                    return
                try:
                    lines.put_nowait(data)
                except queue.Full:
                    failed.set()
                    return
        except (OSError, ValueError):
            failed.set()

    def read(self):
        first = False
        try:
            with self.lock:
                if not self.alive:
                    self._validate()
                    self.lines = queue.Queue(maxsize=1)
                    self.protocol_error = threading.Event()
                    self.process = subprocess.Popen(
                        [str(self.folder / "MeowKeeperCpuSensor.exe")], stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    self.reader = threading.Thread(target=self._drain,
                        args=(self.process, self.lines, self.protocol_error), name="cpu-sensor-pipe", daemon=True)
                    self.reader.start()
                    first = True
                process, lines, failed = self.process, self.lines, self.protocol_error
                request = uuid.uuid4().hex
                process.stdin.write(f"sample {request}\n".encode("ascii"))
                process.stdin.flush()
            # No locks held during sensor IO; shutdown can terminate this owned child.
            data = lines.get(timeout=10 if first else 3)
            payload = json.loads(data.decode("utf-8-sig"))
            if failed.is_set() or not isinstance(payload, dict) or payload.get("request_id") != request:
                raise ValueError()
            names = " ".join(str(s.get("hardware_name", "")) for s in payload.get("sensors", []) if isinstance(s, dict))
            vendor = "intel" if "intel" in names.lower() else "amd" if "amd" in names.lower() else "unknown"
            return select_temperature(payload, vendor=vendor)
        except FileNotFoundError:
            self.close()
            return unavailable("unsupported", "helper_missing", "libre_hardware_monitor")
        except queue.Empty:
            self.close()
            return unavailable("error", "helper_timeout", "libre_hardware_monitor")
        except (OSError, ValueError, TypeError, KeyError):
            self.close()
            return unavailable("error", "helper_invalid", "libre_hardware_monitor")

    def close(self):
        with self.lock:
            process, self.process = self.process, None
            lines = self.lines
        if process is not None:
            try:
                if process.poll() is None:
                    process.stdin.write(b"stop\n")
                    process.stdin.flush()
                    process.wait(timeout=0.5)
            except (OSError, subprocess.TimeoutExpired):
                if process.poll() is None:
                    process.kill()  # Only this Popen-owned child; never by name.
                    process.wait(timeout=1)
            finally:
                for pipe in (process.stdin, process.stdout):
                    if pipe is not None:
                        pipe.close()
                try:
                    lines.put_nowait(b"")  # interrupt an in-flight bounded read during Host shutdown
                except queue.Full:
                    pass


class BrokerCpuProvider:
    """Read-only sensor result; the only writable request is a bounded UTC lease.

    The installer owns/protects this fixed directory and the SYSTEM executable.
    Host never starts an elevated command or supplies an executable/path to it.
    """
    def __init__(self, folder=None):
        self.folder = Path(folder) if folder is not None else Path(
            os.environ.get("ProgramData", r"C:\ProgramData")) / "MeowKeeperPlusCpuSensor"
        self.alive = False

    @property
    def installed(self):
        return (self.folder / "request.txt").is_file()

    def read(self):
        try:
            if self.folder.is_symlink() or (self.folder / "request.txt").is_symlink():
                raise ValueError()
            # Do not replace/create the protected request file or its ACL.
            with (self.folder / "request.txt").open("r+b") as request:
                request.write(datetime.now(timezone.utc).isoformat().encode("ascii"))
                request.truncate()
            cache = self.folder / "temperature.json"
            if not cache.is_file():
                return unavailable(reason="broker_starting", provider="libre_hardware_monitor")
            if cache.is_symlink() or cache.stat().st_size > 65536:
                raise ValueError()
            with cache.open("rb") as source:
                data = source.read(65537)
            if len(data) > 65536:
                raise ValueError()
            payload = json.loads(data.decode("utf-8-sig"))
            if not isinstance(payload, dict) or payload.get("broker_schema") != 1:
                raise ValueError()
            names = " ".join(str(x.get("hardware_name", "")) for x in payload.get("sensors", []) if isinstance(x, dict))
            vendor = "intel" if "intel" in names.lower() else "amd" if "amd" in names.lower() else "unknown"
            result = select_temperature(payload, vendor=vendor)
            age = (datetime.now(timezone.utc) - parse_sensor_time(payload.get("observed_at"))).total_seconds()
            if not -2 <= age <= 35:
                return unavailable(reason="broker_starting", provider="libre_hardware_monitor")
            self.alive = True
            result["transport"] = "protected_local_cache"
            return result
        except FileNotFoundError:
            return unavailable(reason="broker_starting", provider="libre_hardware_monitor")
        except (OSError, ValueError, TypeError, KeyError):
            return unavailable("error", "broker_invalid", "libre_hardware_monitor")

    def close(self):
        self.alive = False
        try:
            with (self.folder / "request.txt").open("r+b") as request:
                request.truncate(0)
        except OSError:
            pass


class InstalledCpuProvider:
    def __init__(self, folder):
        self.broker = BrokerCpuProvider()
        self.helper = HelperCpuProvider(folder)

    @property
    def alive(self):
        return self.broker.alive or self.helper.alive

    def read(self):
        if self.broker.installed:
            return self.broker.read()
        return self.helper.read()

    def close(self):
        self.broker.close()
        self.helper.close()


class CpuTemperatureMonitor:
    def __init__(self, providers, *, active=lambda: True, interval=5, enabled=True):
        if type(interval) not in (int, float) or not 2 <= interval <= 30:
            raise ValueError("CPU溫度採樣間隔須為2～30秒。")
        self.providers, self.active = providers, active
        self.interval, self.enabled = interval, enabled
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.value = unavailable()
        self.failures = 0
        self.retry_at = 0

    def start(self):
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._run, name="cpu-temperature", daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        self._close()
        if self.thread is not None:
            self.thread.join(timeout=4)

    def _close(self):
        for provider in self.providers:
            try:
                provider.close()
            except Exception:  # noqa: BLE001, S110 - sensor cleanup must not break Host shutdown
                pass

    def sample(self):
        if not self.enabled or not self.active():
            self._close()
            with self.lock:
                self.value = unavailable(reason="monitoring_paused" if self.enabled else "disabled")
            return
        if time.monotonic() < self.retry_at:
            return
        result = unavailable()
        for provider in self.providers:
            if self.stop_event.is_set():
                return
            try:
                result = provider.read()
            except Exception:  # noqa: BLE001 - never stop media because a sensor failed
                result = unavailable("error", "sensor_read_failed")
            if result["status"] == "available":
                break
        with self.lock:
            self.value = result
        if result.get("error_reason") == "broker_starting":
            # A minute-triggered task needs a live lease until its next start.
            # Do not clear it through failure backoff/close while waiting.
            self.failures, self.retry_at = 0, 0
            return
        if result["status"] == "available":
            self.failures, self.retry_at = 0, 0
        else:
            self.failures = min(self.failures + 1, 5)
            self.retry_at = time.monotonic() + min(300, 15 * 2 ** self.failures)
            self._close()

    def snapshot(self):
        with self.lock:
            value = dict(self.value)
        if value["status"] == "available":
            try:
                age = (datetime.now(timezone.utc) - parse_sensor_time(value["observed_at"])).total_seconds()
            except (ValueError, TypeError):
                age = float("inf")
            transport = value.get("transport")
            max_age = 40 if transport == "protected_local_cache" else max(15, self.interval * 3)
            if age > max_age or age < -2:
                value.update(status="stale", temperature_c=None, confidence="unavailable", error_reason="sample_expired")
        value.update(enabled=self.enabled, sample_interval_seconds=self.interval,
                     helper_running=any(getattr(p, "alive", False) for p in self.providers))
        if not value.get("hardware_name"):
            value["hardware_name"] = cpu_name()
        return value

    def _run(self):
        try:
            while not self.stop_event.is_set():
                try:
                    self.sample()
                except Exception:  # noqa: BLE001 - diagnostics are lower priority than monitoring
                    with self.lock:
                        self.value = unavailable("error", "sensor_read_failed")
                    self._close()
                self.stop_event.wait(self.interval)
        finally:
            self._close()
