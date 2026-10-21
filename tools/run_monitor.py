"""媒體來源→影像分析→Telegram 推播入口。

範例（YouTube，乾跑不傳訊息）：
    python tools\\run_monitor.py --source-url-env MEOWKEEPER_MEDIA_SOURCE_URL \\
        --analyzer noop --max-frames 10

範例（已存在的本地 YOLO 模型，明確允許傳送圖片）：
    python tools\\run_monitor.py --source-url-env MEOWKEEPER_MEDIA_SOURCE_URL \\
        --analyzer yolo --model models\\cat.onnx --send-telegram \\
        --chat-id 123456789

RTSP 密碼應放在未追蹤的環境變數，不要直接寫在命令列。YouTube 來源會使用固定版
yt-dlp／yt-dlp-ejs 解析，程式不會自動繞過私人內容或下載保存影片。

加上 `--event-video` 時，改用單一 FFmpeg ingest 產生壓縮分段、事件 MP4 與非同步
Telegram 影片；未加此旗標時維持既有的影像通知乾跑流程。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

try:
    from meowkeeper.core.detection.frame_analysis import (
        AnalysisConfigurationError,
        NoopFrameAnalyzer,
        YoloFrameAnalyzer,
    )
    from meowkeeper.core.events.analysis_pipeline import AnalysisPipeline
    from meowkeeper.core.media import (
        ClipAssembler,
        DiskSegmentRingBuffer,
        EventClipRequest,
        EventClipService,
        FfmpegMediaIngest,
        YouTubeSourceAdapter,
        YouTubeSourceConfig,
        YouTubeUnsupportedError,
        is_youtube_url,
    )
    from meowkeeper.core.media.sources import (
        FfmpegFrameSource,
        MediaSourceError,
        YouTubeStreamResolver,
        resolve_source,
    )
    from meowkeeper.core.notifications.telegram import TelegramNotifier
    from meowkeeper.domain.settings import DEFAULT_VISION_ANALYSIS
    from meowkeeper.runtime import default_model_path
    from meowkeeper.storage.database import SQLiteStore
except ImportError:  # 直接以 python tools\run_monitor.py 執行時的開發期 fallback
    ROOT = Path(__file__).resolve().parents[1]
    SRC = ROOT / "src"
    for path in (ROOT, SRC):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from meowkeeper.core.detection.frame_analysis import (
        AnalysisConfigurationError,
        NoopFrameAnalyzer,
        YoloFrameAnalyzer,
    )
    from meowkeeper.core.events.analysis_pipeline import AnalysisPipeline
    from meowkeeper.core.media import (
        ClipAssembler,
        DiskSegmentRingBuffer,
        EventClipRequest,
        EventClipService,
        FfmpegMediaIngest,
        YouTubeSourceAdapter,
        YouTubeSourceConfig,
        YouTubeUnsupportedError,
        is_youtube_url,
    )
    from meowkeeper.core.media.sources import (
        FfmpegFrameSource,
        MediaSourceError,
        YouTubeStreamResolver,
        resolve_source,
    )
    from meowkeeper.core.notifications.telegram import TelegramNotifier
    from meowkeeper.domain.settings import DEFAULT_VISION_ANALYSIS
    from meowkeeper.runtime import default_model_path
    from meowkeeper.storage.database import SQLiteStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="喵喵守護者+：YouTube/RTSP→影格分析；可選事件 MP4 與 Telegram 影片。"
    )
    parser.add_argument(
        "--source-url",
        help="媒體來源網址；RTSP 請改用 --source-url-env，避免密碼出現在命令列。",
    )
    parser.add_argument(
        "--source-url-env",
        default="MEOWKEEPER_MEDIA_SOURCE_URL",
        help="來源網址環境變數名稱，預設為 MEOWKEEPER_MEDIA_SOURCE_URL。",
    )
    parser.add_argument("--quality", default="best", help="YouTube 解析畫質，預設 best。")
    parser.add_argument(
        "--source-type",
        choices=("auto", "youtube", "rtsp", "local_file", "mock"),
        default="auto",
        help="來源類型；預設 auto 依網址判斷。",
    )
    parser.add_argument(
        "--max-height",
        type=int,
        choices=(360, 480, 720, 1080),
        default=720,
        help="YouTube 最高影像高度，預設 720。",
    )
    parser.add_argument(
        "--youtube-video",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="YouTube 是否解析影像，預設啟用。",
    )
    parser.add_argument(
        "--youtube-audio",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="YouTube 是否解析音訊，預設關閉。",
    )
    parser.add_argument("--streamlink-path", help="舊相容參數；正式 YouTube adapter 不使用 streamlink。")
    parser.add_argument("--yt-dlp-path", help="選擇性指定 yt-dlp 可執行檔。")
    parser.add_argument("--ffmpeg-path", help="選擇性指定 ffmpeg 可執行檔。")
    parser.add_argument("--fps", type=int, default=5, help="影格採樣 FPS，MX250 上限 5。")
    parser.add_argument("--width", type=int, default=640, help="分析影像寬度，MX250 上限 640。")
    parser.add_argument("--max-frames", type=int, help="測試用最大影格數；省略表示持續執行。")
    parser.add_argument("--resolve-only", action="store_true", help="只解析來源，不啟動 FFmpeg。")
    parser.add_argument("--analyzer", choices=("noop", "yolo"), default="noop")
    parser.add_argument(
        "--model",
        default=default_model_path(),
        help="本地 YOLO 模型檔；省略時使用專案內預設 models\\yolo11n.pt。",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=DEFAULT_VISION_ANALYSIS.confidence_threshold,
        help="YOLO 貓咪信心門檻，預設 0.45。",
    )
    parser.add_argument("--device", help="選擇性推論裝置，例如 cpu 或 cuda:0。")
    parser.add_argument("--telegram-token-env", default="MEOWKEEPER_TELEGRAM_BOT_TOKEN")
    parser.add_argument("--chat-id", action="append", default=[], help="可重複指定多個 Chat ID。")
    parser.add_argument(
        "--chat-ids-env",
        default="MEOWKEEPER_TELEGRAM_CHAT_IDS",
        help="逗號分隔 Chat ID 的環境變數名稱。",
    )
    parser.add_argument("--send-telegram", action="store_true", help="明確允許傳送分析事件圖片。")
    parser.add_argument("--cooldown", type=float, default=180.0, help="同類事件通知冷卻秒數。")
    parser.add_argument(
        "--event-video",
        action="store_true",
        help="啟用單一 FFmpeg ingest、壓縮環形快取與事件 MP4；預設不對外傳送。",
    )
    parser.add_argument("--ring-directory", default="data/cache/ring", help="壓縮分段快取目錄。")
    parser.add_argument("--event-directory", default="data/events", help="事件 MP4 保存目錄。")
    parser.add_argument("--database-path", default="data/meowkeeper.db", help="SQLite 事件資料庫路徑。")
    parser.add_argument("--event-pre", type=float, default=5.0, help="一般事件前錄秒數。")
    parser.add_argument("--event-post", type=float, default=10.0, help="一般事件後錄秒數。")
    return parser


def _source_value(args: argparse.Namespace) -> str | None:
    if args.source_url:
        return args.source_url
    return os.getenv(args.source_url_env)


def _chat_ids(args: argparse.Namespace) -> list[str]:
    values = list(args.chat_id)
    from_env = os.getenv(args.chat_ids_env, "")
    values.extend(item.strip() for item in from_env.split(",") if item.strip())
    return list(dict.fromkeys(values))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    source_url = _source_value(args)
    if not source_url:
        print("❌ 未提供來源；請使用 --source-url 或設定來源環境變數。", file=sys.stderr)
        return 2
    try:
        source_is_youtube = args.source_type == "youtube" or (
            args.source_type == "auto" and is_youtube_url(source_url)
        )
        resolver = YouTubeStreamResolver(
            streamlink_path=args.streamlink_path,
            yt_dlp_path=args.yt_dlp_path,
        )
        youtube_adapter = None
        if source_is_youtube:
            youtube_adapter = YouTubeSourceAdapter(
                YouTubeSourceConfig(
                    url=source_url,
                    max_height=args.max_height,
                    video=args.youtube_video,
                    audio=args.youtube_audio,
                ),
                yt_dlp_path=args.yt_dlp_path,
                ffmpeg_path=args.ffmpeg_path,
            )
            runtime = youtube_adapter.runtime_check()
            print(json.dumps({"youtube_runtime": runtime.to_dict()}, ensure_ascii=False))
            resolved = youtube_adapter.resolve()
        else:
            resolved = resolve_source(source_url, resolver=resolver, quality=args.quality)
        print(
            json.dumps(
                {
                    "source_type": "youtube" if source_is_youtube else resolved.resolver,
                    "resolver": resolved.resolver,
                    "stream_host": None if source_is_youtube else resolved.safe_host,
                    "youtube": youtube_adapter.media_info.to_public_dict() if youtube_adapter and youtube_adapter.media_info else None,
                    "message_zh_tw": "來源解析成功；實際串流網址只在程序記憶體中使用。",
                },
                ensure_ascii=False,
            )
        )
        if args.resolve_only:
            return 0

        source_identifier = (
            f"youtube:{youtube_adapter.media_info.video_id}"
            if youtube_adapter and youtube_adapter.media_info and youtube_adapter.media_info.video_id
            else ("youtube:unknown" if source_is_youtube else resolved.safe_host)
        )

        if args.analyzer == "yolo":
            if not args.model:
                raise AnalysisConfigurationError(
                    "找不到預設 YOLO 權重 models\\yolo11n.pt；請執行模型取得工具或提供 --model。"
                )
            analyzer = YoloFrameAnalyzer(
                args.model,
                confidence_threshold=args.confidence,
                input_size=args.width,
                device=args.device,
            )
            print(f"推論後端：{analyzer.inference_backend}")
            if analyzer.backend_warning:
                print(f"⚠️ {analyzer.backend_warning}")
        else:
            analyzer = NoopFrameAnalyzer()
            print("⚠️ 目前是 noop 分析模式，不會產生貓咪事件。")

        notifier = None
        chat_ids = _chat_ids(args)
        if args.send_telegram:
            token = os.getenv(args.telegram_token_env)
            if not token:
                raise ValueError("--send-telegram 需要 Telegram Token 環境變數。")
            if not chat_ids:
                raise ValueError("--send-telegram 需要至少一個 Chat ID。")
            notifier = TelegramNotifier(token)

        if args.event_video:
            ring = DiskSegmentRingBuffer(
                args.ring_directory,
                retention_seconds=60,
                segment_seconds=1,
                maximum_size_mb=512,
            )
            store = SQLiteStore(args.database_path)
            clip_service = EventClipService(
                ring,
                ClipAssembler(ffmpeg_path=args.ffmpeg_path),
                output_directory=args.event_directory,
                store=store,
                notifier=notifier,
                chat_ids=chat_ids,
                telegram_enabled=args.send_telegram,
                cooldown_seconds=args.cooldown,
            )
            pipeline = AnalysisPipeline(analyzer, cooldown_seconds=args.cooldown)
            ingest = FfmpegMediaIngest(
                source_url,
                ring,
                resolver=youtube_adapter or resolver,
                ffmpeg_path=args.ffmpeg_path,
                fps=args.fps,
                width=args.width,
            )
            events = []
            try:
                for frame in ingest.iter_frames(max_frames=args.max_frames, resolved=resolved):
                    event = pipeline.process(frame)
                    if event is None:
                        continue
                    events.append(event)
                    clip_service.submit_event(
                        EventClipRequest(
                            event_id=str(uuid.uuid4()),
                            trigger_at=frame.captured_at,
                            event_type=event.analysis.event_type or "cat_detected",
                            source_id=source_identifier,
                            confidence=event.analysis.confidence,
                            pre_seconds=args.event_pre,
                            post_seconds=args.event_post,
                            screenshot_bytes=frame.jpeg_bytes,
                        )
                    )
            finally:
                ingest.close()
                clip_service.wait_for_idle(30.0)
                clip_service.close()
                store.close()
        else:
            source = youtube_adapter or FfmpegFrameSource(
                source_url,
                resolver=resolver,
                ffmpeg_path=args.ffmpeg_path,
                fps=args.fps,
                width=args.width,
            )
            pipeline = AnalysisPipeline(
                analyzer,
                notifier=notifier,
                chat_ids=chat_ids,
                cooldown_seconds=args.cooldown,
            )
            events = pipeline.run(source.iter_frames(max_frames=args.max_frames, resolved=resolved))
        print(f"✅ 監控結束；符合通知策略的事件數：{len(events)}")
        for event in events:
            if args.event_video:
                print(f"⚠️ {event.analysis.message_zh_tw}；事件 MP4 已排入背景佇列。")
            else:
                print(f"⚠️ {event.analysis.message_zh_tw}；已傳送 {len(event.sent)} 個 Chat ID。")
        return 0
    except (MediaSourceError, YouTubeUnsupportedError, AnalysisConfigurationError, ValueError, OSError) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
