"""Phase 0 攝影機探針：TCP 與 RTSP metadata；不執行 ONVIF 寫入。

後續唯讀 ONVIF 待辦：
- 查詢裝置／韌體資訊、Media Profile、串流 URI。
- 查詢 PTZ 能力、狀態與既有 Preset。
- 維持明確 allow-list，禁止影像、網路、帳號、重啟與重設操作。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
from typing import Any

try:
    from .probe_common import (
        ProbeItem,
        ProbeReport,
        ProbeStatus,
        emit_report,
        redact_rtsp_uri,
        run_command,
        sanitize_text,
    )
except ImportError:
    from probe_common import (
        ProbeItem,
        ProbeReport,
        ProbeStatus,
        emit_report,
        redact_rtsp_uri,
        run_command,
        sanitize_text,
    )


def probe_tcp(host: str | None, port: int, timeout_seconds: float) -> ProbeItem:
    if not host:
        return ProbeItem(
            "camera_tcp",
            ProbeStatus.NOT_CHECKED,
            "未提供攝影機 IP；請使用 --host 或 MEOWKEEPER_CAMERA_HOST。",
        )
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            pass
    except (OSError, TimeoutError) as exc:
        return ProbeItem(
            "camera_tcp",
            ProbeStatus.ERROR,
            "無法連上攝影機 RTSP 連接埠；請確認同一網路、IP、連接埠與防火牆。",
            {"host": host, "port": port, "error_type": type(exc).__name__},
        )
    return ProbeItem(
        "camera_tcp",
        ProbeStatus.OK,
        "攝影機 RTSP 連接埠可建立 TCP 連線。",
        {"host": host, "port": port},
    )


def _stream_summary(payload: dict[str, Any]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for stream in payload.get("streams", []):
        summaries.append(
            {
                key: stream.get(key)
                for key in (
                    "index",
                    "codec_type",
                    "codec_name",
                    "profile",
                    "width",
                    "height",
                    "r_frame_rate",
                    "avg_frame_rate",
                    "sample_rate",
                    "channels",
                )
                if stream.get(key) is not None
            }
        )
    return summaries


def probe_rtsp(rtsp_uri: str | None, timeout_seconds: float) -> ProbeItem:
    if not rtsp_uri:
        return ProbeItem(
            "rtsp_stream",
            ProbeStatus.NOT_CHECKED,
            "未提供 RTSP 環境變數；不會猜測 D-Link 串流路徑。",
        )
    if not rtsp_uri.lower().startswith(("rtsp://", "rtsps://")):
        return ProbeItem(
            "rtsp_stream",
            ProbeStatus.ERROR,
            "環境變數內容不是有效的 RTSP URI，請檢查格式。",
        )
    ffprobe = shutil.which("ffprobe")
    safe_uri = redact_rtsp_uri(rtsp_uri)
    if not ffprobe:
        return ProbeItem(
            "rtsp_stream",
            ProbeStatus.NOT_CHECKED,
            "找不到 ffprobe；已驗證 URI 格式，但尚未讀取影音 metadata。",
            {"uri": safe_uri},
        )

    result = run_command(
        [
            ffprobe,
            "-v",
            "error",
            "-rtsp_transport",
            "tcp",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            rtsp_uri,
        ],
        timeout_seconds=timeout_seconds,
    )
    if result.timed_out:
        return ProbeItem(
            "rtsp_stream",
            ProbeStatus.ERROR,
            "RTSP 檢測逾時；請檢查串流路徑、帳密、同時連線數與網路。",
            {"uri": safe_uri},
        )
    if result.returncode != 0:
        safe_error = sanitize_text(result.stderr[-1000:], [rtsp_uri])
        return ProbeItem(
            "rtsp_stream",
            ProbeStatus.ERROR,
            "FFprobe 無法讀取 RTSP；請檢查路徑、帳密與攝影機可用串流數。",
            {"uri": safe_uri, "ffprobe_error": safe_error.strip()},
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return ProbeItem(
            "rtsp_stream",
            ProbeStatus.ERROR,
            "FFprobe 已執行，但回傳內容不是可解析的 JSON。",
            {"uri": safe_uri},
        )

    streams = _stream_summary(payload)
    has_video = any(stream.get("codec_type") == "video" for stream in streams)
    has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
    status = ProbeStatus.OK if has_video else ProbeStatus.WARNING
    message = (
        "已讀取 RTSP 影像與音訊 metadata。"
        if has_video and has_audio
        else "已讀取 RTSP 影像，但目前沒有偵測到音訊。"
        if has_video
        else "RTSP 有回應，但目前沒有偵測到影像 stream。"
    )
    return ProbeItem(
        "rtsp_stream",
        status,
        message,
        {"uri": safe_uri, "streams": streams, "has_video": has_video, "has_audio": has_audio},
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="唯讀檢查攝影機 TCP 與 RTSP metadata；不修改 ONVIF 設定。"
    )
    parser.add_argument(
        "--host",
        default=os.getenv("MEOWKEEPER_CAMERA_HOST"),
        help="攝影機區域網路 IP 或主機名；也可用 MEOWKEEPER_CAMERA_HOST。",
    )
    parser.add_argument("--port", type=int, default=554, help="RTSP TCP 連接埠，預設 554。")
    parser.add_argument(
        "--rtsp-url-env",
        default="MEOWKEEPER_RTSP_URL",
        help="保存完整 RTSP URI 的環境變數名稱，不要直接把密碼放在命令列。",
    )
    parser.add_argument("--timeout", type=float, default=8.0, help="每項檢測逾時秒數。")
    parser.add_argument("--output", help="選擇性 JSON 報告輸出路徑。")
    return parser


def collect_report(
    host: str | None,
    port: int,
    rtsp_uri: str | None,
    timeout_seconds: float,
) -> ProbeReport:
    return ProbeReport(
        "camera",
        [
            probe_tcp(host, port, timeout_seconds),
            probe_rtsp(rtsp_uri, timeout_seconds),
            ProbeItem(
                "onvif",
                ProbeStatus.NOT_CHECKED,
                "ONVIF 唯讀能力探針尚待選定相依套件；本工具不會修改攝影機。",
            ),
        ],
    )


def main() -> int:
    args = build_parser().parse_args()
    rtsp_uri = os.getenv(args.rtsp_url_env)
    report = collect_report(args.host, args.port, rtsp_uri, args.timeout)
    emit_report(report, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
