"""go2rtc 官方 binary 的 localhost bridge 生命週期邊界。"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from meowkeeper.process_guard import CrossProcessLock, port_owner


@dataclass(frozen=True)
class Go2rtcManifest:
    version: str
    source_repository: str
    license: str
    file_size_bytes: int
    sha256: str


class Go2rtcProcess:
    OFFICIAL_REPOSITORY = "https://github.com/AlexxIT/go2rtc"

    def __init__(self, *, executable: str | Path | None = None, bind_host: str = "127.0.0.1") -> None:
        self.root = Path(__file__).resolve().parents[4]
        self.executable = Path(executable).resolve() if executable else self.detect_executable()
        self.bind_host = bind_host
        self.process: subprocess.Popen[bytes] | None = None
        self._instance_lock: CrossProcessLock | None = None
        self.restart_count = 0
        if bind_host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("go2rtc 管理與 restream 只允許 localhost。")

    @staticmethod
    def detect_executable() -> Path | None:
        root = Path(__file__).resolve().parents[4]
        candidates = (
            root / "vendor" / "go2rtc" / "v1.9.14" / "go2rtc.exe",
            root / "vendor" / "go2rtc" / "go2rtc.exe",
            root / "vendor" / "go2rtc" / "go2rtc",
        )
        return next((item for item in candidates if item.is_file()), None)

    @staticmethod
    def manifest_for(path: Path, *, version: str, license_name: str) -> Go2rtcManifest:
        if not path.is_file():
            raise FileNotFoundError(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return Go2rtcManifest(version, Go2rtcProcess.OFFICIAL_REPOSITORY, license_name, path.stat().st_size, digest)

    def status(self) -> dict[str, Any]:
        if self.executable is None:
            return {"status": "not_checked", "installed": False, "message_zh_tw": "尚未提供官方 go2rtc binary。"}
        if self.process and self.process.poll() is None:
            return {"status": "running", "installed": True, "bind_host": self.bind_host, "external": False}
        # This compatibility class predates the richer localhost process
        # manager, but it must not start a second vendor binary when an
        # already-running MeowKeeper+ go2rtc owns the standard ports.
        vendor_root = (self.root / "vendor" / "go2rtc").resolve()
        for port in (1984, 8554):
            for owner in port_owner(port):
                try:
                    owner_path = Path(owner.executable or "").resolve()
                    if owner_path.is_relative_to(vendor_root):
                        return {"status": "running", "installed": True, "bind_host": self.bind_host, "external": True}
                except (OSError, RuntimeError, ValueError):
                    continue
        return {"status": "stopped", "installed": True, "bind_host": self.bind_host, "external": False}

    def build_command(self, config_path: str | Path) -> list[str]:
        if self.executable is None:
            raise FileNotFoundError("缺少官方 go2rtc binary。")
        return [str(self.executable), "-config", str(Path(config_path).resolve())]

    def start(self, config_path: str | Path) -> dict[str, Any]:
        if self.executable is None:
            return self.status()
        if self.status().get("status") == "running":
            return self.status()
        instance_lock = CrossProcessLock("MeowKeeperPlusGo2Rtc", root=self.root)
        if not instance_lock.acquire():
            return {**self.status(), "status": "locked", "message_zh_tw": "go2rtc 單一實例鎖已被占用，未啟動第二個程序。"}
        self._instance_lock = instance_lock
        try:
            self.process = subprocess.Popen(self.build_command(config_path), shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, ValueError):
            instance_lock.release()
            self._instance_lock = None
            raise
        return self.status()

    def stop(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
        if self._instance_lock is not None:
            self._instance_lock.release()
            self._instance_lock = None
