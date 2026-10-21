"""go2rtc process lifecycle with an isolated API and explicit RTSP LAN opt-in."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

from meowkeeper.process_guard import CrossProcessLock, port_owner

from .config import write_config
from .downloader import OfficialGo2RtcDownloader
from .health import http_health, port_health
from .manifest import load_manifest, manifest_path, verify_manifest
from .models import Go2RtcConfig, Go2RtcStatus
from .ptz_backend import PTZ_VERSION, VerifiedPtzBackend, ptz_backend_selected
from .redaction import redact_diagnostic_text, redact_secrets


class Go2RtcProcess:
    def __init__(
        self,
        root: str | Path,
        *,
        api_port: int = 1984,
        rtsp_port: int = 8554,
        rtsp_host: str = "127.0.0.1",
        rtsp_lan_firewall_ready: bool = False,
        log_path: str | Path | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.api_port = api_port
        self.rtsp_port = rtsp_port
        if rtsp_host not in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}:
            raise ValueError("go2rtc RTSP bind 只允許 localhost 或 0.0.0.0。")
        self.rtsp_host = rtsp_host
        self.rtsp_lan_firewall_ready = bool(rtsp_lan_firewall_ready)
        self.experimental_ptz = ptz_backend_selected(self.root)
        self.downloader = VerifiedPtzBackend(self.root) if self.experimental_ptz else OfficialGo2RtcDownloader(self.root)
        self.process: subprocess.Popen[str] | None = None
        self._log_handle = None
        self._log_thread: threading.Thread | None = None
        self._instance_lock: CrossProcessLock | None = None
        self.restart_count = 0
        local_appdata = os.getenv("LOCALAPPDATA") if os.name == "nt" else None
        # Only the release port pair may reuse the user's authenticated
        # AppData session.  Custom ports are always isolated under the
        # repository so tests and diagnostics can never overwrite it.
        preferred_runtime_dir = (
            Path(local_appdata) / "MeowKeeperPlus" / "go2rtc"
            if local_appdata and (api_port, rtsp_port) == (1984, 8554)
            else None
        )
        runtime_dir = preferred_runtime_dir or self.root / "data" / "go2rtc"
        if preferred_runtime_dir is not None:
            try:
                preferred_runtime_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                # Restricted development sandboxes may deny AppData.  Keep
                # the same secret-free runtime config local to the repository;
                # normal Windows installs use LOCALAPPDATA.
                runtime_dir = self.root / "data" / "go2rtc"
        config_name = "config.yaml" if (api_port, rtsp_port) == (1984, 8554) else f"config-{api_port}-{rtsp_port}.yaml"
        self.config_path = runtime_dir / config_name
        self.log_path = Path(log_path).resolve() if log_path else self.root / "logs" / "go2rtc.log"

    @property
    def lock_name(self) -> str:
        """Return the singleton name for this API/RTSP port pair.

        The release ports retain the stable public lock name.  Test or
        explicitly isolated instances use a port-scoped suffix so they can
        coexist without weakening the one-process rule for 1984/8554.
        """

        if (self.api_port, self.rtsp_port) == (1984, 8554):
            return "MeowKeeperPlusGo2Rtc"
        return f"MeowKeeperPlusGo2Rtc_{self.api_port}_{self.rtsp_port}"

    def _close_log_handle(self) -> None:
        if self._log_thread is not None:
            self._log_thread.join(timeout=2.0)
            self._log_thread = None
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None

    @staticmethod
    def _pump_redacted_log(stream: object, handle: object) -> None:
        """Write only redacted go2rtc output to the persistent log."""

        readline = getattr(stream, "readline", None)
        if not callable(readline):
            return
        try:
            while True:
                line = readline()
                if not line:
                    break
                safe_line = redact_diagnostic_text(line, limit=8192)
                handle.write(safe_line)
                handle.flush()
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()

    @property
    def executable(self) -> Path:
        return self.downloader.executable

    @property
    def api_url(self) -> str:
        return f"http://127.0.0.1:{self.api_port}"

    def _child_environment(self) -> dict[str, str]:
        """Expose bundled FFmpeg to go2rtc snapshots without editing its session config."""

        environment = os.environ.copy()
        ffmpeg_dir = self.root / "vendor" / "ffmpeg" / "bin"
        if (ffmpeg_dir / "ffmpeg.exe").is_file():
            environment["PATH"] = str(ffmpeg_dir) + os.pathsep + environment.get("PATH", "")
        return environment

    @property
    def rtsp_url(self) -> str:
        return f"rtsp://127.0.0.1:{self.rtsp_port}"

    def install(self, *, force: bool = False) -> Go2RtcStatus:
        try:
            manifest = self.downloader.ensure_installed(force=force)
        except (OSError, RuntimeError, ValueError) as exc:
            return Go2RtcStatus("failed", False, False, message_zh_tw=redact_secrets(str(exc)))
        return Go2RtcStatus("ready", True, False, version=manifest.release_tag, api_url=self.api_url, rtsp_url=self.rtsp_url, executable=self.executable, message_zh_tw="PTZ 實驗後端已驗證。" if self.experimental_ptz else "官方 go2rtc 已驗證。")

    def start(self, streams: dict[str, str] | None = None) -> Go2RtcStatus:
        if self.rtsp_host == "0.0.0.0" and not self.rtsp_lan_firewall_ready:
            return Go2RtcStatus(
                "failed",
                self.executable.is_file(),
                False,
                api_url=self.api_url,
                rtsp_url=self.rtsp_url,
                message_zh_tw="RTSP 區網 bind 已拒絕：尚未確認 Private／localsubnet 防火牆規則。",
            )
        if self.process is not None and self.process.poll() is None:
            return self.status()
        # A healthy localhost process may be the user's already-authenticated
        # official go2rtc session.  Reuse it and never overwrite its config.
        existing = self.status()
        if existing.healthy:
            return existing
        # Never hide an unrelated listener or race it into a second go2rtc.
        # ``port_owner`` returns executable and command-line evidence, so the
        # caller can report a safe diagnostic without terminating that process.
        occupied = port_owner(self.api_port) + port_owner(self.rtsp_port)
        if occupied:
            return Go2RtcStatus(
                "failed",
                existing.installed,
                False,
                version=existing.version,
                api_url=self.api_url,
                rtsp_url=self.rtsp_url,
                executable=self.executable if existing.installed else None,
                message_zh_tw="go2rtc 目標 port 已被其他程序占用，未啟動第二個程序。",
            )
        instance_lock = CrossProcessLock(self.lock_name, root=self.root)
        if not instance_lock.acquire():
            existing = self.status()
            if existing.healthy:
                return existing
            return Go2RtcStatus(
                "failed",
                existing.installed,
                False,
                version=existing.version,
                api_url=self.api_url,
                rtsp_url=self.rtsp_url,
                executable=self.executable if existing.installed else None,
                message_zh_tw="go2rtc 單一實例鎖已被占用，未啟動第二個程序。",
            )
        self._instance_lock = instance_lock
        # Re-check both health and port ownership after taking the cross-
        # process lock.  A competing launcher may have completed between the
        # initial read and mutex acquisition.
        existing = self.status()
        occupied = port_owner(self.api_port) + port_owner(self.rtsp_port)
        if existing.healthy:
            instance_lock.release()
            self._instance_lock = None
            return existing
        if occupied:
            instance_lock.release()
            self._instance_lock = None
            return Go2RtcStatus(
                "failed",
                existing.installed,
                False,
                version=existing.version,
                api_url=self.api_url,
                rtsp_url=self.rtsp_url,
                executable=self.executable if existing.installed else None,
                message_zh_tw="go2rtc 目標 port 已被其他程序占用，未啟動第二個程序。",
            )
        installed = self.install()
        if not installed.installed:
            instance_lock.release()
            self._instance_lock = None
            return installed
        try:
            if self.experimental_ptz:
                self.downloader.ensure_installed()
            else:
                verify_manifest(self.root, self.executable)
            config = Go2RtcConfig(
                api_host="127.0.0.1",
                api_port=self.api_port,
                rtsp_host=self.rtsp_host,
                rtsp_port=self.rtsp_port,
                streams=streams or {},
            )
            # An existing config may contain the user's authenticated Xiaomi
            # session.  Starting the bundled binary must never replace it with
            # an empty generated config.  Reuse it only when its API/RTSP
            # listen ports match this managed instance; otherwise use a
            # repository-local, port-scoped config without touching AppData.
            if self.config_path.is_file() and not self._existing_config_matches_ports(self.config_path):
                # Unknown or mismatched config may contain a valid Xiaomi
                # session.  Fail closed instead of replacing it or silently
                # starting a fresh unauthenticated session elsewhere.
                instance_lock.release()
                self._instance_lock = None
                return Go2RtcStatus(
                    "failed", True, False,
                    message_zh_tw="既有 go2rtc 設定與目標 port 不符或無法確認；為保護小米登入狀態，未覆寫設定。",
                )
            if not self._existing_config_matches_ports(self.config_path):
                try:
                    write_config(self.config_path, config)
                except OSError:
                    fallback = self.root / "data" / "go2rtc" / f"config-{self.api_port}.yaml"
                    if fallback.resolve() == self.config_path.resolve() or fallback.is_file():
                        raise
                    self.config_path = fallback
                    write_config(self.config_path, config)
            elif self.config_path.is_file():
                # Preserve authenticated Xiaomi/session sections while making
                # both listener boundaries explicit. In particular, the
                # shorthand ``listen: :1984`` must not expose the API.
                self._ensure_listener_bindings(self.config_path)
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            if self.log_path.is_file() and self.log_path.stat().st_size > 5 * 1024 * 1024:
                rotated = self.log_path.with_suffix(self.log_path.suffix + ".1")
                rotated.unlink(missing_ok=True)
                self.log_path.replace(rotated)
            log_handle = self.log_path.open("a", encoding="utf-8")
            self._log_handle = log_handle
            self.process = subprocess.Popen(
                [str(self.executable), "-config", str(self.config_path)],
                cwd=str(self.root),
                env=self._child_environment(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                shell=False,
                text=True,
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self._log_thread = threading.Thread(
                target=self._pump_redacted_log,
                args=(self.process.stdout, log_handle),
                name="meowkeeper-go2rtc-log",
                daemon=True,
            )
            self._log_thread.start()
        except (OSError, ValueError, RuntimeError) as exc:
            if self._log_handle is not None:
                self._log_handle.close()
                self._log_handle = None
            self.process = None
            instance_lock.release()
            self._instance_lock = None
            return Go2RtcStatus("failed", True, False, message_zh_tw=redact_secrets(f"go2rtc 啟動失敗：{exc}"))
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self._close_log_handle()
                instance_lock.release()
                self._instance_lock = None
                return self.status(message="go2rtc 程序意外結束。")
            if http_health(self.api_url) and port_health("127.0.0.1", self.rtsp_port):
                return self.status()
            time.sleep(0.2)
        return self.status(message="go2rtc 未在逾時內通過 localhost 健康檢查。")

    def _existing_config_matches_ports(self, path: Path) -> bool:
        """Return whether an existing config can be reused without rewriting.

        This is deliberately a conservative text check: it does not parse or
        log the Xiaomi credentials/session.  A config with an unknown layout is
        treated as non-reusable and a local generated config is selected.
        """

        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return False
        if not text.strip():
            return False
        ports: dict[str, int] = {}
        section: str | None = None
        for raw_line in text.splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if raw_line[:1] not in {" ", "\t"} and stripped.endswith(":"):
                section = stripped[:-1].strip().lower()
                continue
            if section not in {"api", "rtsp"} or not stripped.lower().startswith("listen:"):
                continue
            value = stripped.split(":", 1)[1].strip()
            candidate = value.rsplit(":", 1)[-1].strip()
            if candidate.isdigit():
                ports[section] = int(candidate)
        return bool(
            ports.get("api") == self.api_port
            and ports.get("rtsp") == self.rtsp_port
        )

    def _ensure_listener_bindings(self, path: Path) -> None:
        """Tighten only API/RTSP listen lines without touching session data."""

        try:
            original = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return
        lines = original.splitlines(keepends=True)
        section: str | None = None
        output: list[str] = []
        changed = False
        for line in lines:
            stripped = line.strip()
            if line[:1] not in {" ", "\t"} and stripped.endswith(":"):
                section = stripped[:-1].strip().lower()
            if section in {"api", "rtsp"} and stripped.lower().startswith("listen:"):
                value = stripped.split(":", 1)[1].strip()
                candidate = value.rsplit(":", 1)[-1].strip()
                expected_port = self.api_port if section == "api" else self.rtsp_port
                if candidate.isdigit() and int(candidate) == expected_port:
                    expected_host = "127.0.0.1" if section == "api" else self.rtsp_host
                    newline = "\n" if line.endswith("\n") else ""
                    indent = line[: len(line) - len(line.lstrip(" \t"))]
                    replacement = f"{indent}listen: {expected_host}:{expected_port}{newline}"
                    if replacement != line:
                        line = replacement
                        changed = True
            output.append(line)
        if not changed:
            return
        temporary = path.with_suffix(path.suffix + ".bind.tmp")
        try:
            temporary.write_text("".join(output), encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def stop(self) -> Go2RtcStatus:
        process = self.process
        self.process = None
        if process is None:
            if self._instance_lock is not None:
                self._instance_lock.release()
                self._instance_lock = None
            return self.status(message="go2rtc 由外部程序管理；MeowKeeper+ 未執行停止。")
        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5.0)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=3.0)
            except (OSError, subprocess.TimeoutExpired):
                return self.status(message="go2rtc 停止逾時。")
        self._close_log_handle()
        if self._instance_lock is not None:
            self._instance_lock.release()
            self._instance_lock = None
        return self.status(message="go2rtc 已停止。")

    def restart(self, streams: dict[str, str] | None = None) -> Go2RtcStatus:
        self.stop()
        self.restart_count += 1
        return self.start(streams=streams)

    def watchdog(self, streams: dict[str, str] | None = None) -> Go2RtcStatus:
        """Perform one bounded recovery check; callers decide polling cadence."""

        if self.process is None or self.process.poll() is not None:
            return self.restart(streams=streams) if self.executable.is_file() else self.status()
        return self.status()

    def read_recent_log(self, *, max_chars: int = 4000) -> str:
        """Read a bounded, redacted tail of the merged go2rtc log.

        go2rtc's stdout and stderr are intentionally merged into the same
        rotating file.  Returning only a redacted tail keeps login diagnostics
        useful without exposing cookies, URLs or credentials.
        """

        try:
            with self.log_path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - max(1024, max_chars * 4)))
                raw = handle.read()
        except OSError:
            return ""
        return redact_diagnostic_text(raw.decode("utf-8", errors="replace"), limit=max_chars)

    def read_recent_stderr(self, *, max_chars: int = 4000) -> str:
        """Compatibility name for callers that need the merged stderr tail."""

        return self.read_recent_log(max_chars=max_chars)

    def status(self, *, message: str | None = None) -> Go2RtcStatus:
        installed = self.executable.is_file() and (self.root / "vendor" / "go2rtc" / "model_manifest.json").is_file()
        running = self.process is not None and self.process.poll() is None
        localhost_healthy = http_health(self.api_url) and port_health("127.0.0.1", self.rtsp_port)
        # A separately launched official go2rtc (for example the user's
        # existing authenticated session) is still a valid local bridge.  We
        # report it as externally owned instead of claiming it has our PID;
        # lifecycle methods never stop a process we did not start.
        healthy = localhost_healthy and (running or self.process is None)
        version: str | None = None
        if installed:
            try:
                if self.experimental_ptz:
                    self.downloader.ensure_installed()
                    version = PTZ_VERSION
                else:
                    version = load_manifest(manifest_path(self.root)).release_tag
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                installed = False
        return Go2RtcStatus(
            "running" if healthy else ("ready" if installed else "not_installed"),
            installed,
            healthy,
            pid=self.process.pid if running and self.process else None,
            version=version,
            api_url=self.api_url,
            rtsp_url=self.rtsp_url,
            message_zh_tw=message or ("go2rtc 運作中（目前由外部程序管理）。" if healthy and not running else ("go2rtc 運作中。" if healthy else "go2rtc 尚未啟動。")),
            executable=self.executable if installed else None,
        )
