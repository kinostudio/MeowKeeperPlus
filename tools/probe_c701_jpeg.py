"""Probe one C701 frame through the existing localhost go2rtc session.

The Xiaomi source URL stays in memory. Only a verified JPEG is saved under
the repository's ignored data directory; diagnostics never print secrets.
"""

from __future__ import annotations

import json
import secrets
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meowkeeper.integrations.go2rtc.client import Go2RtcClient
from meowkeeper.integrations.xiaomi.device_discovery import Go2RtcXiaomiDiscovery
from meowkeeper.runtime import bundled_binary

MODEL = "chuangmi.camera.079ae2"
OUTPUT = ROOT / "data" / "diagnostics" / "c701-jpeg-probe.jpg"


def _category(stderr: bytes) -> str:
    message = stderr.decode("utf-8", errors="replace").lower()
    for marker, category in (
        ("no p2pid", "no_p2pid"),
        ("read udp", "udp_read_failure"),
        ("i/o timeout", "timeout"),
        ("timed out", "timeout"),
        ("404", "rtsp_404"),
        ("403", "rtsp_403"),
        ("invalid data", "invalid_data"),
    ):
        if marker in message:
            return category
    return "other"


def main() -> int:
    report: dict[str, object] = {
        "model": MODEL,
        "region": "sg",
        "profile": "subtype=1",
        "jpeg_valid": False,
        "temporary_stream_removed": False,
    }
    client = Go2RtcClient(timeout_seconds=10)
    stream_name = "mk_c701_jpeg_" + secrets.token_hex(5)
    created = False
    try:
        accounts = client.xiaomi_accounts()
        report["account_count"] = len(accounts)
        if len(accounts) != 1:
            report["error_category"] = "account_count_not_one"
            return 2
        sources = client.xiaomi_sources(accounts[0], region="sg")
        matching = [
            item["url"]
            for item in sources
            if parse_qs(urlsplit(str(item["url"])).query).get("model", [""])[0] == MODEL
        ]
        report["model_match_count"] = len(matching)
        if len(matching) != 1:
            report["error_category"] = "model_match_count_not_one"
            return 2
        ffmpeg = bundled_binary("ffmpeg")
        if not ffmpeg:
            report["error_category"] = "bundled_ffmpeg_missing"
            return 2
        source = Go2RtcXiaomiDiscovery._with_profile(matching[0], "subtype=1")
        client.put_stream(stream_name, source)
        created = True
        report["stream_put"] = "success"
        command = [
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
            "-rtsp_transport", "tcp", "-i", f"rtsp://127.0.0.1:8554/{stream_name}",
            "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, timeout=35, check=False, shell=False,
            )
        except subprocess.TimeoutExpired:
            report["error_category"] = "ffmpeg_timeout_35s"
            return 1
        report["ffmpeg_returncode"] = completed.returncode
        jpeg = completed.stdout
        dimensions = Go2RtcXiaomiDiscovery._jpeg_dimensions(jpeg)
        valid = bool(
            completed.returncode == 0
            and 4 <= len(jpeg) <= 2 * 1024 * 1024
            and jpeg.startswith(b"\xff\xd8")
            and jpeg.endswith(b"\xff\xd9")
            and dimensions
        )
        report["jpeg_valid"] = valid
        if not valid:
            report["error_category"] = _category(completed.stderr)
            return 1
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_bytes(jpeg)
        report["jpeg_path"] = str(OUTPUT)
        report["jpeg_bytes"] = len(jpeg)
        report["width"], report["height"] = dimensions
        return 0
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        report["error_category"] = "probe_error"
        report["error_type"] = type(exc).__name__
        return 2
    finally:
        if created:
            try:
                client.delete_stream(stream_name)
                report["temporary_stream_removed"] = True
            except (OSError, RuntimeError, ValueError):
                report["temporary_stream_removed"] = False
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
