"""唯讀檢查公開 YouTube adapter 所需 runtime。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from meowkeeper.core.media.youtube import check_youtube_runtime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="唯讀檢查 YouTube yt-dlp、FFmpeg 與 JavaScript runtime。")
    parser.add_argument("--yt-dlp-path")
    parser.add_argument("--ffmpeg-path")
    parser.add_argument("--ffprobe-path")
    parser.add_argument("--js-runtime-path")
    parser.add_argument("--output", help="選擇性輸出 JSON 報告路徑。")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = check_youtube_runtime(
        yt_dlp_path=args.yt_dlp_path,
        ffmpeg_path=args.ffmpeg_path,
        ffprobe_path=args.ffprobe_path,
        js_runtime_path=args.js_runtime_path,
    ).to_dict()
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        target = Path(args.output).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
