"""Run a single, isolated go2rtc Xiaomi trace probe.

This diagnostic intentionally starts the official binary itself so stdout can
be consumed in memory and redacted before it is persisted.  The source URL,
account reference and all session values remain process-local and are never
written to a report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from meowkeeper.integrations.go2rtc.client import Go2RtcClient
from meowkeeper.integrations.go2rtc.redaction import (
    mask_response_body,
    redact_diagnostic_text,
)

_PRIVATE_FIELD = re.compile(
    r"(?i)(p2p[_-]?id|did|mac|client[_-]?(?:public|private)|device[_-]?public|"
    r"sign|uid|account|username|email|token|password|secret)(\s*[=:]\s*)[^,;\s}]+"
)
_P2P_VALUE = re.compile(r"(?i)p2p[_-]?id(?:\s*[=:]\s*)([A-Za-z0-9+/=_-]+)")
_VENDOR = re.compile(r"(?i)\b(?:vendor|vendor_name)\b(?:\s*[=:]\s*)([A-Za-z0-9_.-]+)")
_ENCODED_XIAOMI_SOURCE = re.compile(r"(?i)(src=)(?:xiaomi%3a|xiaomi:)[^\s&]+")
_IPV4 = re.compile(r"(?<![\d.])(\d{1,3})(?:\.(\d{1,3})){3}(?![\d.])")
_MAC = re.compile(r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}:){5}[0-9a-f]{2}(?![0-9a-f])")


def safe_trace_line(line: str) -> str:
    """Redact URLs and provider identifiers before writing trace evidence."""

    # go2rtc's trace prints URL-encoded API query parameters, so the normal
    # URL redactor cannot see ``xiaomi%3A%2F%2F...``.  Remove the entire source
    # value before applying the common redaction rules.
    text = _ENCODED_XIAOMI_SOURCE.sub(r"\1<url redacted>", line)
    text = redact_diagnostic_text(text, limit=8192)
    text = _IPV4.sub(lambda match: ".".join(match.group(0).split(".")[:3] + ["xxx"]), text)
    text = _MAC.sub("xx:xx:xx:xx:xx:xx", text)
    return _PRIVATE_FIELD.sub(r"\1\2<redacted>", text)


def safe_trace_summary(lines: list[str]) -> dict[str, Any]:
    raw = "".join(lines)
    p2p_values = _P2P_VALUE.findall(raw)
    vendors = [value.lower() for value in _VENDOR.findall(raw)]
    safe_vendor = next((value for value in vendors if value in {"cs2", "tutk", "agora", "mtp"}), None)
    return {
        "trace_line_count": len(lines),
        "trace_sha256": hashlib.sha256("".join(safe_trace_line(line) for line in lines).encode("utf-8")).hexdigest(),
        "vendor_observation": safe_vendor or "not_exposed_or_not_observed",
        "has_p2p_id": None,
        "p2p_id_observed_in_trace": bool(p2p_values),
        "p2p_id_length": len(p2p_values[0]) if p2p_values else None,
        "p2p_id_prefix_length": None,
    }


def api_request(client: Go2RtcClient, path: str, *, method: str = "GET", params: dict[str, str] | None = None) -> dict[str, Any]:
    """Use the existing localhost client transport while retaining status."""

    response = client._request(path, method=method, params=params)
    return {
        "http_status": response.get("status"),
        "content_type": response.get("content_type"),
        "body_masked": mask_response_body(response.get("data"), limit=1024),
        "data": response.get("data"),
    }


def status_counts(payload: Any) -> tuple[int, int]:
    if not isinstance(payload, dict):
        return 0, 0
    producers = payload.get("producers")
    consumers = payload.get("consumers")
    producer_count = len(producers) if isinstance(producers, list) else int(producers or 0) if isinstance(producers, (int, float)) else 0
    consumer_count = len(consumers) if isinstance(consumers, list) else int(consumers or 0) if isinstance(consumers, (int, float)) else 0
    return producer_count, consumer_count


def read_process_output(process: subprocess.Popen[str], lines: list[str]) -> None:
    stream = process.stdout
    if stream is None:
        return
    for line in stream:
        # Keep only bounded in-memory evidence.  The original line is not
        # persisted; safe_trace_line is applied after the process exits.
        if len(lines) < 20_000:
            lines.append(line)


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def choose_source(client: Go2RtcClient, model: str) -> tuple[str, str]:
    accounts = client.xiaomi_accounts()
    if not accounts:
        raise RuntimeError("隔離 go2rtc 尚未載入 Xiaomi session。")
    sources = client.xiaomi_sources(accounts[0], region="cn")
    for source in sources:
        source_url = str(source.get("url") or "")
        query = dict(item.split("=", 1) for item in source_url.split("?", 1)[-1].split("&") if "=" in item)
        if query.get("model") == model:
            return source_url, "xiaomi_camera_" + hashlib.sha256((model + "\x00" + source_url).encode()).hexdigest()[:10]
    raise RuntimeError("指定 Xiaomi 型號不在已載入裝置清單。")


def main() -> int:
    parser = argparse.ArgumentParser(description="隔離 go2rtc Xiaomi 單一 subtype=sd trace probe")
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-port", type=int, default=21984)
    parser.add_argument("--rtsp-port", type=int, default=28554)
    parser.add_argument("--config", type=Path, default=ROOT / "reports" / "phase3c4" / "standalone-go2rtc" / "config.yaml")
    parser.add_argument("--workdir", type=Path, default=ROOT / "reports" / "phase3c4" / "standalone-go2rtc")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    exe = ROOT / "vendor" / "go2rtc" / "v1.9.14" / "go2rtc.exe"
    if not exe.is_file() or not args.config.is_file():
        raise SystemExit("找不到官方 go2rtc v1.9.14 或隔離設定。")

    args.output = args.output.resolve()
    args.config = args.config.resolve()
    args.workdir = args.workdir.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    trace_log = args.output.with_suffix(".trace.log")
    process = subprocess.Popen(
        [str(exe), "-config", str(args.config)],
        cwd=str(args.workdir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    lines: list[str] = []
    reader = threading.Thread(target=read_process_output, args=(process, lines), daemon=True)
    reader.start()
    client = Go2RtcClient(f"http://127.0.0.1:{args.api_port}", timeout_seconds=5.0)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            if client.get("/api") is not None:
                break
        except RuntimeError:
            time.sleep(0.25)
    result: dict[str, Any] = {
        "schema_version": 1,
        "go2rtc_release": "v1.9.14",
        "model": args.model,
        "region": "cn",
        "profile": "subtype=sd",
        "api": f"127.0.0.1:{args.api_port}",
        "rtsp": f"127.0.0.1:{args.rtsp_port}",
        "process_pid": process.pid,
        "source_internal_id": None,
        "session_account_count": None,
        "put": None,
        "open": None,
        "status_before_open": None,
        "status_after_open": None,
        "producer_peak": 0,
        "consumer_peak": 0,
        "first_video_packet": False,
        "first_jpeg": False,
        "classification": "unknown",
    }
    stream_name = "phase3c5_trace_" + hashlib.sha256(args.model.encode()).hexdigest()[:12]
    try:
        accounts = client.xiaomi_accounts()
        result["session_account_count"] = len(accounts)
        source, internal_id = choose_source(client, args.model)
        result["source_internal_id"] = internal_id
        # Add subtype without retaining a second copy of the source in the report.
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

        parsed = urlsplit(source)
        query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != "subtype"]
        query.append(("subtype", "sd"))
        source_sd = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
        before = api_request(client, "/api/streams", params={"src": stream_name})
        result["status_before_open"] = {key: before[key] for key in ("http_status", "content_type", "body_masked")}
        put = api_request(client, "/api/streams", method="PUT", params={"name": stream_name, "src": source_sd})
        result["put"] = {key: put[key] for key in ("http_status", "content_type", "body_masked")}
        if put["http_status"] and 200 <= int(put["http_status"]) < 300:
            opened = api_request(
                client,
                "/api/streams",
                params={"src": stream_name, "video": "all", "audio": "all", "microphone": ""},
            )
            result["open"] = {
                key: opened[key] for key in ("http_status", "content_type", "body_masked")
            }
            result["open"]["response_sha256"] = hashlib.sha256(
                str(opened.get("data") or "").encode("utf-8", errors="replace")
            ).hexdigest()
            for _ in range(4):
                status = api_request(client, "/api/streams", params={"src": stream_name})
                result["status_after_open"] = {key: status[key] for key in ("http_status", "content_type", "body_masked")}
                producer_count, consumer_count = status_counts(status["data"])
                result["producer_peak"] = max(result["producer_peak"], producer_count)
                result["consumer_peak"] = max(result["consumer_peak"], consumer_count)
                time.sleep(0.5)
            safe_open = str((result.get("open") or {}).get("body_masked") or "").lower()
            if "p2pid" in safe_open:
                # The go2rtc error text alone does not prove which Xiaomi
                # request failed or whether the cloud allocated an ID.
                result["classification"] = "P2PID_ERROR_TEXT_ONLY_UNVERIFIED"
                # A failed dial can leave a configured producer entry in the
                # /api/streams response.  It is not an active producer.  The
                # trace's ``dial ... prod=0`` is the authoritative count.
                if re.search(r"(?i)\bdial\b[^\n]*\bprod=0\b", "".join(lines)):
                    result["producer_peak"] = 0
            elif result["open"].get("http_status") and int(result["open"]["http_status"]) >= 400:
                result["classification"] = "XIAOMI_MISS_VENDOR_API_FAILURE"
            elif result["producer_peak"] > 0:
                result["classification"] = "STREAM_OPENED_NEEDS_JPEG_TEST"
            else:
                result["classification"] = "NO_PRODUCER_UNKNOWN_FAILURE"
    except Exception as exc:  # noqa: BLE001  # pragma: no cover - diagnostic boundary
        result["classification"] = "DIAGNOSTIC_ERROR"
        result["error_safe"] = redact_diagnostic_text(str(exc), limit=512)
    finally:
        try:
            api_request(client, "/api/streams", method="DELETE", params={"src": stream_name})
        except Exception as exc:  # noqa: BLE001 - cleanup must continue
            result["cleanup_error_type"] = type(exc).__name__
        stop_process(process)
        reader.join(timeout=2)

    safe_lines = [safe_trace_line(line) for line in lines]
    trace_log.write_text("".join(safe_lines), encoding="utf-8")
    result["trace"] = safe_trace_summary(lines)
    result["trace_log"] = str(trace_log.relative_to(ROOT)).replace("\\", "/")
    result["process_exit_code"] = process.returncode
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("model", "process_pid", "session_account_count", "put", "open", "producer_peak", "consumer_peak", "classification", "trace")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
