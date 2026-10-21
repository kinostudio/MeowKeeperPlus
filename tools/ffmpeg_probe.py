"""唯讀檢查 FFmpeg、FFprobe、硬體加速與常用解碼器。"""

from __future__ import annotations

import argparse
import shutil

try:
    from meowkeeper.runtime import bundled_binary
except ImportError:
    bundled_binary = shutil.which

try:
    from .probe_common import ProbeItem, ProbeReport, ProbeStatus, emit_report, run_command
except ImportError:
    from probe_common import ProbeItem, ProbeReport, ProbeStatus, emit_report, run_command


def _check_binary(name: str, executable: str | None) -> ProbeItem:
    if not executable:
        return ProbeItem(
            name,
            ProbeStatus.NOT_CHECKED,
            f"找不到內含的 {name}；請修復或重新安裝 MeowKeeper+ runtime。",
        )
    result = run_command([executable, "-version"], timeout_seconds=10)
    if result.timed_out:
        return ProbeItem(name, ProbeStatus.ERROR, f"{name} 版本檢查逾時。", {"path": executable})
    if result.returncode != 0:
        return ProbeItem(
            name,
            ProbeStatus.ERROR,
            f"{name} 無法正常啟動，請檢查安裝檔與 PATH。",
            {"path": executable},
        )
    first_line = result.stdout.splitlines()[0] if result.stdout else "未提供版本文字"
    return ProbeItem(
        name,
        ProbeStatus.OK,
        f"{name} 可正常執行。",
        {"path": executable, "version": first_line},
    )


def _probe_capabilities(ffmpeg: str | None) -> ProbeItem:
    if not ffmpeg:
        return ProbeItem(
            "ffmpeg_capabilities",
            ProbeStatus.NOT_CHECKED,
            "缺少 FFmpeg，因此尚未檢查硬體加速與 H.264/H.265 解碼器。",
        )

    hwaccels = run_command([ffmpeg, "-hide_banner", "-hwaccels"], timeout_seconds=10)
    decoders = run_command([ffmpeg, "-hide_banner", "-decoders"], timeout_seconds=10)
    if hwaccels.timed_out or decoders.timed_out:
        return ProbeItem(
            "ffmpeg_capabilities",
            ProbeStatus.ERROR,
            "FFmpeg 能力檢查逾時。",
            {"path": ffmpeg},
        )
    if hwaccels.returncode != 0 or decoders.returncode != 0:
        return ProbeItem(
            "ffmpeg_capabilities",
            ProbeStatus.ERROR,
            "FFmpeg 無法列出硬體加速或解碼器；請檢查安裝內容。",
            {"path": ffmpeg},
        )

    acceleration_methods = [
        line.strip()
        for line in hwaccels.stdout.splitlines()
        if line.strip() and "Hardware acceleration methods" not in line
    ]
    relevant_decoders = [
        line.strip()
        for line in decoders.stdout.splitlines()
        if "h264" in line.casefold() or "hevc" in line.casefold()
    ]
    has_h264 = any("h264" in line.casefold() for line in relevant_decoders)
    has_hevc = any("hevc" in line.casefold() for line in relevant_decoders)
    status = ProbeStatus.OK if has_h264 else ProbeStatus.WARNING
    message = (
        "已列出硬體加速與 H.264/H.265 相關解碼器。"
        if has_h264 and has_hevc
        else "已列出 FFmpeg 能力，但缺少 H.264 或 H.265 相關解碼器。"
    )
    return ProbeItem(
        "ffmpeg_capabilities",
        status,
        message,
        {
            "hardware_acceleration_methods": acceleration_methods,
            "h264_decoder_available": has_h264,
            "hevc_decoder_available": has_hevc,
            "relevant_decoders": relevant_decoders,
        },
    )


def collect_report() -> ProbeReport:
    ffmpeg = bundled_binary("ffmpeg")
    ffprobe = bundled_binary("ffprobe")
    return ProbeReport(
        "ffmpeg",
        [
            _check_binary("ffmpeg", ffmpeg),
            _check_binary("ffprobe", ffprobe),
            _probe_capabilities(ffmpeg),
        ],
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="唯讀檢查 FFmpeg、FFprobe、硬體加速與解碼能力。"
    )
    parser.add_argument("--output", help="選擇性 JSON 報告輸出路徑。")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    emit_report(collect_report(), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
