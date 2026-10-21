"""One bounded, reversible Windows test of Xiaomi MISS PTZ on 026c02.

Run only after the owner explicitly accepts a brief interruption of the
installed SYSTEM Core. Never print or persist Xiaomi credentials or sources.
This is an isolated acceptance test, not a production PTZ installation.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import io
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import psutil
import yaml
from PIL import Image, ImageChops, ImageStat

ROOT = Path(__file__).resolve().parents[1]
INSTALLED = Path(r"C:\ProgramData\MeowKeeperPlus\App")
OFFICIAL_EXE = INSTALLED / "vendor" / "go2rtc" / "v1.9.14" / "go2rtc.exe"
FORK_EXE = ROOT / "reports" / "ptz-fixed-source" / "go2rtc-ptz-e746789.exe"
FFMPEG_EXE = ROOT / "vendor" / "ffmpeg" / "bin" / "ffmpeg.exe"
FORK_SHA256 = "3AFA1B357D80E49A8149C34FB663BA2E0803709172D6B98AA0A25F09FB8F33CF"
TASK = "MeowKeeperPlus Core"
SOURCE_STREAM = "xiaomi_camera_2"
TRIAL_STREAM = "ptz_trial_026c02"
OFFICIAL_API = "http://127.0.0.1:1984"
TRIAL_API = "http://127.0.0.1:21984"
SAFE_ERROR_CODES = {
    "ambiguous_port_owner",
    "official_host_or_go2rtc_missing",
    "host_identity_unverified",
    "go2rtc_identity_unverified",
    "core_task_unavailable",
    "core_task_identity_unverified",
    "isolated_binary_hash_mismatch",
    "official_binary_missing",
    "source_not_single_xiaomi",
    "wrong_camera_model",
    "isolated_ports_in_use",
    "official_processes_did_not_stop",
    "trial_api_not_ready",
    "single_trial_producer_not_verified",
    "trial_consumer_did_not_start",
    "trial_producer_not_active",
    "ptz_state_not_ready",
    "ptz_stop_not_acknowledged",
    "trial_frame_unavailable",
}


def _request(url: str, *, method: str = "GET", data: bytes | None = None, timeout: float = 8) -> bytes:
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(2 * 1024 * 1024)


def _json(
    url: str, *, method: str = "GET", body: dict[str, Any] | None = None, timeout: float = 8
) -> Any:
    data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
    return json.loads(_request(url, method=method, data=data, timeout=timeout))


def _port_owner(port: int) -> psutil.Process | None:
    owners = {
        conn.pid
        for conn in psutil.net_connections(kind="tcp")
        if conn.status == psutil.CONN_LISTEN and conn.laddr.port == port and conn.pid
    }
    if len(owners) > 1:
        raise RuntimeError("ambiguous_port_owner")
    return psutil.Process(next(iter(owners))) if owners else None


def _is_under(path: Path, root: Path) -> bool:
    return os.path.commonpath((str(path.resolve()), str(root.resolve()))).casefold() == str(root.resolve()).casefold()


def _verify_owners() -> tuple[psutil.Process, psutil.Process]:
    host = _port_owner(8000)
    go2rtc = _port_owner(1984)
    if host is None or go2rtc is None:
        raise RuntimeError("official_host_or_go2rtc_missing")
    if not _is_under(Path(host.exe()), INSTALLED) or not any(
        str(arg).lower().endswith("run_host.py") for arg in host.cmdline()
    ):
        raise RuntimeError("host_identity_unverified")
    if Path(go2rtc.exe()).resolve() != OFFICIAL_EXE.resolve() or go2rtc.ppid() != host.pid:
        raise RuntimeError("go2rtc_identity_unverified")
    return host, go2rtc


def _wait_verified_owners(timeout: float) -> tuple[psutil.Process, psutil.Process]:
    """Allow the installed Core's normal startup to finish before preflight."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return _verify_owners()
        except RuntimeError as exc:
            if str(exc) != "official_host_or_go2rtc_missing" or time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def _verify_task() -> None:
    result = subprocess.run(
        ["schtasks.exe", "/Query", "/TN", TASK, "/XML"],
        capture_output=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("core_task_unavailable")
    xml = result.stdout.decode("utf-16" if result.stdout.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8", errors="replace")
    if str(INSTALLED).casefold() not in xml.casefold():
        raise RuntimeError("core_task_identity_unverified")


def _verify_binary() -> None:
    if not FORK_EXE.is_file() or hashlib.sha256(FORK_EXE.read_bytes()).hexdigest().upper() != FORK_SHA256:
        raise RuntimeError("isolated_binary_hash_mismatch")
    if not OFFICIAL_EXE.is_file():
        raise RuntimeError("official_binary_missing")
    if not FFMPEG_EXE.is_file():
        raise RuntimeError("trial_consumer_did_not_start")


def _minimal_config() -> dict[str, Any]:
    raw = _request(f"{OFFICIAL_API}/api/config")
    config = yaml.safe_load(raw)
    if not isinstance(config, dict) or not isinstance(config.get("xiaomi"), dict):
        raise TypeError("xiaomi_session_unavailable")
    streams = config.get("streams")
    source = streams.get(SOURCE_STREAM) if isinstance(streams, dict) else None
    values = source if isinstance(source, list) else [source]
    if len(values) != 1 or not isinstance(values[0], str) or not values[0].startswith("xiaomi://"):
        raise RuntimeError("source_not_single_xiaomi")
    if "model=chuangmi.camera.026c02" not in values[0]:
        raise RuntimeError("wrong_camera_model")
    return {
        "api": {"listen": "127.0.0.1:21984"},
        "rtsp": {"listen": "127.0.0.1:28554"},
        "webrtc": {"listen": ""},
        "xiaomi": config["xiaomi"],
        "streams": {TRIAL_STREAM: values[0]},
    }


def _task_action(action: str) -> None:
    result = subprocess.run(
        ["schtasks.exe", action, "/TN", TASK], capture_output=True, timeout=15, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"core_task_{action.strip('/').lower()}_failed")


def _wait_ports_closed(timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(_port_owner(port) is None for port in (8000, 1984, 8554)):
            return True
        time.sleep(0.4)
    return False


def _wait_url(url: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            _request(url, timeout=2)
            return True
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.4)
    return False


def _safe_ptz_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"connected": False}
    position = value.get("position")
    return {
        "connected": value.get("connected") is True,
        "can_left": value.get("can_left") is True,
        "can_right": value.get("can_right") is True,
        "position_present": isinstance(position, dict),
        "angle": position.get("angle") if isinstance(position, dict) else None,
        "elevation": position.get("elevation") if isinstance(position, dict) else None,
        "ret": position.get("ret") if isinstance(position, dict) else None,
    }


def _capture_frame(path: Path, *, newer_than: int = 0) -> tuple[bytes, int]:
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        try:
            stamp = path.stat().st_mtime_ns
            if stamp > newer_than and 4 <= path.stat().st_size <= 2 * 1024 * 1024:
                data = path.read_bytes()
                with Image.open(io.BytesIO(data)) as image:
                    if image.format != "JPEG":
                        raise ValueError("format")
                    image.verify()
                return data, stamp
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    raise RuntimeError("trial_frame_unavailable")


def _frame_evidence(before: bytes, after: bytes, directory: Path) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "before.jpg").write_bytes(before)
    (directory / "after.jpg").write_bytes(after)
    with Image.open(io.BytesIO(before)) as first, Image.open(io.BytesIO(after)) as second:
        comparable = first.size == second.size
        difference = (sum(ImageStat.Stat(ImageChops.difference(
            first.convert("RGB"), second.convert("RGB"),
        )).mean) / 3 if comparable else None)
        return {"before_sha256": hashlib.sha256(before).hexdigest(),
                "after_sha256": hashlib.sha256(after).hexdigest(),
                "width": first.width, "height": first.height,
                "same_dimensions": comparable, "mean_pixel_difference": difference,
                "physical_movement_verified": False, "visual_review_required": True}


def _trial(config_path: Path, report: dict[str, Any], *, allow_relative_without_position: bool = False,
           directions: tuple[str, ...] = (), position_only: bool = False) -> None:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        [str(FORK_EXE), "-config", str(config_path)],
        cwd=config_path.parent,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    consumer: subprocess.Popen[bytes] | None = None
    try:
        if not _wait_url(f"{TRIAL_API}/api/streams", 12):
            raise RuntimeError("trial_api_not_ready")
        # A configured source is not an active PTZ producer. Keep exactly one
        # local RTSP consumer alive while testing; the installed Core is stopped.
        latest_frame = config_path.parent / "latest.jpg"
        consumer = subprocess.Popen(
            [str(FFMPEG_EXE), "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp",
             "-i", f"rtsp://127.0.0.1:28554/{TRIAL_STREAM}", "-map", "0:v:0", "-an",
             "-vf", "fps=2,scale=w='min(640,iw)':h=-2", "-c:v", "mjpeg", "-q:v", "3",
             "-f", "image2", "-update", "1", "-atomic_writing", "1", "-y", str(latest_frame)],
            cwd=config_path.parent,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        ptz_url = f"{TRIAL_API}/api/xiaomi/ptz?" + urllib.parse.urlencode({"src": TRIAL_STREAM})
        deadline = time.monotonic() + 18
        while time.monotonic() < deadline:
            streams = _json(f"{TRIAL_API}/api/streams")
            active = streams.get(TRIAL_STREAM) if isinstance(streams, dict) else None
            producers = active.get("producers", []) if isinstance(active, dict) else []
            consumers = active.get("consumers", []) if isinstance(active, dict) else []
            report["producer_count"] = len(producers) if isinstance(producers, list) else 0
            report["consumer_count"] = len(consumers) if isinstance(consumers, list) else 0
            if report["producer_count"] > 1 or report["consumer_count"] > 1:
                raise RuntimeError("single_trial_producer_not_verified")
            if consumer.poll() is not None:
                raise RuntimeError("trial_consumer_did_not_start")
            if report["producer_count"] == 1 and report["consumer_count"] == 1:
                try:
                    state = _safe_ptz_state(_json(ptz_url))
                    if state["connected"]:
                        break
                except urllib.error.HTTPError as exc:
                    if exc.code != 503:
                        raise
            time.sleep(0.5)
        else:
            raise RuntimeError("trial_producer_not_active")
        cached_state = state
        report["stage"] = "ptz_refresh"
        try:
            before = _safe_ptz_state(_json(ptz_url + "&refresh=true"))
        except urllib.error.HTTPError as exc:
            body = exc.read(4096).decode("utf-8", errors="replace").lower()
            report["ptz_http_status"] = exc.code
            report["ptz_error_category"] = (
                "response_timeout" if "timeout" in body
                else "malformed_motor_response" if "malformed motor response" in body
                else "producer_disconnected" if "disconnected" in body
                else "producer_not_active" if "not active" in body
                else "other"
            )
            if position_only:
                report["position_readback_verified"] = False
                report["stage"] = "position_readback_failed"
                return
            if not (allow_relative_without_position and exc.code == 504
                    and report["ptz_error_category"] == "response_timeout"):
                raise
            # Explicit limited relative test; never pretend cached state is a
            # successful position read or allow absolute/privacy positioning.
            report["position_readback_verified"] = False
            report["relative_without_position"] = True
            before = cached_state
        report["ptz_before"] = before
        if position_only:
            valid = (before.get("ret") == 0 and before.get("connected") is True
                     and all(type(before.get(key)) is int and 0 <= before[key] <= 101
                             for key in ("angle", "elevation")))
            report["position_readback_verified"] = valid
            report["stage"] = "position_readback_verified" if valid else "position_readback_failed"
            return
        direction = directions[0] if directions else "left" if before["can_left"] else "right" if before["can_right"] else None
        if not before["connected"] or direction is None:
            raise RuntimeError("ptz_state_not_ready")
        report["trial_direction"] = direction
        if directions == ("up", "down"):
            report["direction_trials"] = []
            for vertical_direction in directions:
                result = _vertical_trial(ptz_url, latest_frame, vertical_direction)
                report["direction_trials"].append(result)
            report["physical_movement_verified"] = False
            report["stage"] = "vertical_commands_sent_visual_verification_required"
            return
        before_frame, _ = _capture_frame(latest_frame)
        move_sent = False
        try:
            # A network error can occur after the command reaches the camera.
            # Always send stop once the move request has been attempted.
            move_sent = True
            result = _safe_ptz_state(
                _json(ptz_url, method="POST", body={"action": "move", "direction": direction}, timeout=2)
            )
            report["move_command_sent"] = result["connected"]
            report["move_acknowledged"] = False
            time.sleep(0.25)
        finally:
            if move_sent:
                for _ in range(3):
                    try:
                        stopped = _safe_ptz_state(
                            _json(ptz_url, method="POST", body={"action": "stop"}, timeout=2)
                        )
                        report["stop_command_sent"] = stopped["connected"]
                        report["stop_acknowledged"] = False
                        if report["stop_command_sent"]:
                            break
                    except (OSError, ValueError, urllib.error.URLError):
                        time.sleep(0.25)
                if not report.get("stop_command_sent"):
                    raise RuntimeError("ptz_stop_not_acknowledged")
        # Require a frame written after stop, not a cached pre-command JPEG.
        after_frame, _ = _capture_frame(latest_frame, newer_than=time.time_ns())
        private_root = ROOT / "data" / "ptz-trials"
        private_root.mkdir(parents=True, exist_ok=True)
        evidence = Path(tempfile.mkdtemp(prefix="026c02-", dir=private_root))
        report["frame_evidence"] = _frame_evidence(before_frame, after_frame, evidence)
        report["private_evidence_reference"] = evidence.name
        report["physical_movement_verified"] = False
        if report.get("relative_without_position"):
            report["ptz_after"] = _safe_ptz_state(_json(ptz_url))
            report["physical_movement_verified"] = False
            report["stage"] = "relative_commands_sent_visual_verification_required"
        else:
            report["ptz_after"] = _safe_ptz_state(_json(ptz_url + "&refresh=true"))
            report["position_readback_verified"] = True
            report["stage"] = "commands_sent_visual_verification_required"
    finally:
        if consumer is not None and consumer.poll() is None:
            consumer.terminate()
            try:
                consumer.wait(timeout=5)
            except subprocess.TimeoutExpired:
                consumer.kill()
                consumer.wait(timeout=3)
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def _vertical_trial(ptz_url: str, latest_frame: Path, direction: str) -> dict[str, Any]:
    """One bounded move; connection/write success is never a motor ACK."""
    result: dict[str, Any] = {"direction": direction, "move_command_sent": False,
                              "stop_command_sent": False, "motor_acknowledged": False,
                              "physical_movement_verified": False}
    before, _ = _capture_frame(latest_frame)
    try:
        state = _safe_ptz_state(_json(ptz_url, method="POST",
                                    body={"action": "move", "direction": direction}, timeout=2))
        result["move_command_sent"] = state["connected"]
        time.sleep(0.4)
    finally:
        for _ in range(3):
            try:
                state = _safe_ptz_state(_json(ptz_url, method="POST", body={"action": "stop"}, timeout=2))
                result["stop_command_sent"] = state["connected"]
                if result["stop_command_sent"]:
                    break
            except (OSError, ValueError, urllib.error.URLError):
                time.sleep(0.25)
        if not result["stop_command_sent"]:
            raise RuntimeError("ptz_stop_not_acknowledged")
    # Allow the encoded/decoded video pipeline to catch up after the command.
    time.sleep(2)
    after, _ = _capture_frame(latest_frame, newer_than=time.time_ns())
    private_root = ROOT / "data" / "ptz-trials"
    private_root.mkdir(parents=True, exist_ok=True)
    evidence = Path(tempfile.mkdtemp(prefix=f"026c02-{direction}-", dir=private_root))
    result["frame_evidence"] = _frame_evidence(before, after, evidence)
    result["private_evidence_reference"] = evidence.name
    return result


def run(*, allow_relative_without_position: bool = False, directions: tuple[str, ...] = (),
        position_only: bool = False) -> dict[str, Any]:
    report: dict[str, Any] = {
        "model": "chuangmi.camera.026c02",
        "isolated_api_port": 21984,
        "official_binary_replaced": False,
        "stage": "preflight",
        "producer_count": 0,
        "move_acknowledged": False,
        "stop_acknowledged": False,
        "official_monitor_restored": False,
    }
    if not ctypes.windll.shell32.IsUserAnAdmin():
        report["error_code"] = "administrator_required"
        return report
    core_stopped = False
    host: psutil.Process | None = None
    go2rtc: psutil.Process | None = None
    try:
        _verify_binary()
        _verify_task()
        host, go2rtc = _wait_verified_owners(15)
        if _port_owner(21984) is not None or _port_owner(28554) is not None:
            raise RuntimeError("isolated_ports_in_use")
        minimal = _minimal_config()
        with tempfile.TemporaryDirectory(prefix="MeowKeeper-Ptz-026c02-") as temporary:
            config_path = Path(temporary) / "config.yaml"
            config_path.write_text(yaml.safe_dump(minimal, allow_unicode=True), encoding="utf-8")
            report["stage"] = "stopping_core"
            _task_action("/End")
            core_stopped = True
            if not _wait_ports_closed(12):
                # Only the two identities verified before ending the task may
                # be stopped; never terminate by python/go2rtc process name.
                for known in (host, go2rtc):
                    if known and known.is_running():
                        known.terminate()
                if not _wait_ports_closed(8):
                    raise RuntimeError("official_processes_did_not_stop")
            report["stage"] = "isolated_ptz_trial"
            _trial(config_path, report, allow_relative_without_position=allow_relative_without_position,
                   directions=directions, position_only=position_only)
    except Exception as exc:  # noqa: BLE001 - report only the safe class/code
        code = str(exc) if isinstance(exc, RuntimeError) else ""
        report["error_code"] = code if code in SAFE_ERROR_CODES else type(exc).__name__
        report["stage"] = "trial_failed"
    finally:
        if core_stopped:
            try:
                _task_action("/Run")
                report["official_monitor_restored"] = _wait_url("http://127.0.0.1:8000/healthz", 35)
            except Exception as exc:  # noqa: BLE001
                report["restore_error_type"] = type(exc).__name__
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="隔離測試 026c02 Xiaomi MISS PTZ；會短暫中斷正式監控。")
    parser.add_argument("--confirm-interruption", action="store_true", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--allow-relative-without-position", action="store_true",
                        help="明確允許位置讀回逾時時做一次短方向／停止測試；不代表已驗證實體移動。")
    parser.add_argument("--vertical-only", action="store_true", help="僅測試一次上移與一次下移，不測左右。")
    parser.add_argument("--position-only", action="store_true", help="只查詢一次位置，不發送任何移動命令。")
    parser.add_argument("--feedback-fix", action="store_true", help="使用 SHA-256 固定的獨立位置回饋修正版；只允許位置查詢。")
    args = parser.parse_args()
    if args.feedback_fix:
        if not args.position_only:
            parser.error("位置回饋修正版本輪只允許 --position-only。")
        FORK_EXE = ROOT / "reports" / "ptz-feedback-fix" / "go2rtc-ptz-feedback.exe"
        FORK_SHA256 = "3F64D4C5144350DC816ABB4DE41BD686E248A9528EB2825A00CD8FC65EFEA0F3"
    result = run(allow_relative_without_position=args.allow_relative_without_position,
                 directions=("up", "down") if args.vertical_only else (), position_only=args.position_only)
    result["experimental_binary_sha256"] = FORK_SHA256
    result["feedback_fix_enabled"] = args.feedback_fix
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    accepted = (result.get("stage") == "trial_complete"
                or (args.position_only and result.get("position_readback_verified") is True))
    raise SystemExit(0 if accepted and result["official_monitor_restored"] else 1)
