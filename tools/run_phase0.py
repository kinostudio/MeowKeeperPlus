"""彙整 Phase 0 唯讀探針；不安裝套件、不修改攝影機、不傳送 Telegram 訊息。"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from typing import Any

try:
    from .camera_probe import collect_report as collect_camera_report
    from .ffmpeg_probe import collect_report as collect_ffmpeg_report
    from .probe_common import ProbeStatus, emit_json
    from .system_probe import collect_report as collect_system_report
    from .telegram_probe import collect_report as collect_telegram_report
except ImportError:
    from camera_probe import collect_report as collect_camera_report
    from ffmpeg_probe import collect_report as collect_ffmpeg_report
    from probe_common import ProbeStatus, emit_json
    from system_probe import collect_report as collect_system_report
    from telegram_probe import collect_report as collect_telegram_report


def _overall_status(reports: list[dict[str, Any]]) -> str:
    statuses = {
        item["status"]
        for report in reports
        for item in report.get("items", [])
    }
    if ProbeStatus.ERROR.value in statuses:
        return ProbeStatus.ERROR.value
    if ProbeStatus.WARNING.value in statuses:
        return ProbeStatus.WARNING.value
    if ProbeStatus.NOT_CHECKED.value in statuses:
        return ProbeStatus.NOT_CHECKED.value
    return ProbeStatus.OK.value


def collect_phase0_report(
    profile_name: str = "mx250_minimum",
    camera_host: str | None = None,
    camera_port: int = 554,
    rtsp_uri: str | None = None,
    telegram_token: str | None = None,
    timeout_seconds: float = 8.0,
) -> dict[str, Any]:
    """執行安全的唯讀檢查；Telegram 永遠以 send_test=False 呼叫。"""

    reports = [
        collect_system_report(profile_name).as_dict(),
        collect_ffmpeg_report().as_dict(),
        collect_camera_report(camera_host, camera_port, rtsp_uri, timeout_seconds).as_dict(),
        collect_telegram_report(telegram_token, send_test=False).as_dict(),
    ]
    return {
        "schema_version": 1,
        "probe": "phase0",
        "profile": profile_name,
        "overall_status": _overall_status(reports),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment_note_zh_tw": (
            "本報告描述目前執行探針的開發機；非 MX250 主機的結果不得當成產品最低基線效能證明。"
        ),
        "safety_note_zh_tw": (
            "本入口不安裝套件、不修改 ONVIF、不猜測 RTSP 路徑，也不傳送 Telegram 訊息。"
        ),
        "reports": reports,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="執行 MeowKeeper+ Phase 0 唯讀相容性檢查。")
    parser.add_argument(
        "--profile",
        default="mx250_minimum",
        help="效能 profile 名稱，預設 mx250_minimum。",
    )
    parser.add_argument(
        "--camera-host",
        default=os.getenv("MEOWKEEPER_CAMERA_HOST"),
        help="攝影機 IP 或主機名；未提供時回報 not_checked。",
    )
    parser.add_argument("--camera-port", type=int, default=554, help="RTSP TCP 連接埠。")
    parser.add_argument(
        "--rtsp-url-env",
        default="MEOWKEEPER_RTSP_URL",
        help="保存完整 RTSP URI 的環境變數名稱。",
    )
    parser.add_argument(
        "--telegram-token-env",
        default="MEOWKEEPER_TELEGRAM_BOT_TOKEN",
        help="保存 Telegram Bot Token 的環境變數名稱。",
    )
    parser.add_argument("--timeout", type=float, default=8.0, help="網路檢測逾時秒數。")
    parser.add_argument("--output", help="選擇性 JSON 報告輸出路徑。")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        payload = collect_phase0_report(
            profile_name=args.profile,
            camera_host=args.camera_host,
            camera_port=args.camera_port,
            rtsp_uri=os.getenv(args.rtsp_url_env),
            telegram_token=os.getenv(args.telegram_token_env),
            timeout_seconds=args.timeout,
        )
    except ValueError as exc:
        build_parser().error(str(exc))
    emit_json(payload, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
