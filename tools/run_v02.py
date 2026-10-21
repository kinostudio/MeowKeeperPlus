"""V0.2 追蹤／身分／規則行為乾跑入口；不另開 RTSP。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from meowkeeper.core.behavior import (
    BehaviorConfig,
    BehaviorEngine,
    BehaviorFeatureExtractor,
    ZoneRegistry,
)
from meowkeeper.core.events.v02_pipeline import V02AnalysisPipeline
from meowkeeper.core.identity import (
    DinoV2Embedder,
    DINOv2Unavailable,
    IdentityClassifier,
    IdentityPrediction,
    PetRegistry,
)
from meowkeeper.core.media.sources import (
    FfmpegFrameSource,
    MediaSourceError,
    YouTubeStreamResolver,
    is_youtube_url,
    resolve_source,
)
from meowkeeper.core.media.youtube import YouTubeSourceAdapter, YouTubeSourceConfig
from meowkeeper.core.notifications.telegram import TelegramNotifier
from meowkeeper.core.tracking import BoTSORTTracker, TrackHistory
from meowkeeper.storage import SQLiteStore


class _DisabledIdentityClassifier:
    class _Backend:
        model_version = "identity-disabled"
        backend_name = "disabled"

    embedder = _Backend()

    def classify(self, _crop: bytes, *, track_id: str | int | None = None) -> IdentityPrediction:
        return IdentityPrediction(
            None,
            None,
            0.0,
            0.0,
            0.0,
            1.0,
            self.embedder.model_version,
            track_id,
            False,
            "尚未登錄寵物或身分分析已停用。",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="喵喵守護者+ V0.2 多貓追蹤與行為基礎乾跑。")
    parser.add_argument("--source-url", help="RTSP、YouTube 或本地影片來源。")
    parser.add_argument("--source-url-env", default="MEOWKEEPER_MEDIA_SOURCE_URL")
    parser.add_argument("--max-frames", type=int, default=10)
    parser.add_argument("--fps", type=int, default=5)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--confidence", type=float, default=0.45)
    parser.add_argument("--device", help="選擇性指定 YOLO 裝置，例如 cpu 或 cuda:0。")
    parser.add_argument("--pets-root", type=Path, default=ROOT / "data" / "pets")
    parser.add_argument("--database", type=Path, default=ROOT / "data" / "meowkeeper.sqlite3")
    parser.add_argument("--identity-model", type=Path)
    parser.add_argument("--disable-identity", action="store_true")
    parser.add_argument("--telegram-token-env", default="MEOWKEEPER_TELEGRAM_BOT_TOKEN")
    parser.add_argument("--chat-id", action="append", default=[])
    parser.add_argument("--send-telegram", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    source_url = args.source_url or os.getenv(args.source_url_env)
    if not source_url:
        print("❌ 未提供來源；請使用 --source-url 或來源環境變數。", file=sys.stderr)
        return 2
    try:
        resolver = YouTubeStreamResolver()
        if is_youtube_url(source_url):
            youtube_adapter = YouTubeSourceAdapter(
                YouTubeSourceConfig(source_url, max_height=720),
                ffmpeg_path=None,
            )
            resolved = youtube_adapter.resolve()
        else:
            youtube_adapter = None
            resolved = resolve_source(source_url, resolver=resolver)
        registry = PetRegistry(args.pets_root)
        pets = registry.list_pets(enabled_only=True)
        if args.disable_identity or not any(profile.embedding_count > 0 for profile in pets):
            identity = _DisabledIdentityClassifier()
            identity_status = "disabled（沒有可用寵物 embedding）"
        else:
            embedder = DinoV2Embedder(model_path=args.identity_model)
            identity = IdentityClassifier(registry, embedder)
            identity_status = f"{embedder.backend_name}；{embedder.model_version}"
        tracker = BoTSORTTracker(confidence_threshold=args.confidence, device=args.device)
        history = TrackHistory()
        pipeline = V02AnalysisPipeline(
            tracker,
            identity,
            history=history,
            feature_extractor=BehaviorFeatureExtractor(history, ZoneRegistry()),
            behavior_engine=BehaviorEngine(BehaviorConfig()),
            store=SQLiteStore(args.database),
            notifier=(TelegramNotifier(os.getenv(args.telegram_token_env, "")) if args.send_telegram else None),
            chat_ids=tuple(args.chat_id),
        )
        source = youtube_adapter or FfmpegFrameSource(
            source_url, resolver=resolver, fps=args.fps, width=args.width
        )
        print(json.dumps({"resolver": resolved.resolver, "identity": identity_status}, ensure_ascii=False))
        for frame in source.iter_frames(max_frames=args.max_frames, resolved=resolved):
            result = pipeline.process(frame)
            print(
                json.dumps(
                    {
                        "sequence": frame.sequence,
                        "tracks": len(result.tracks),
                        "events": len(result.finalized_events),
                        "warnings": list(result.warnings),
                    },
                    ensure_ascii=False,
                )
            )
        pipeline.flush()
        print(json.dumps({"metrics": pipeline.performance_report()}, ensure_ascii=False))
        pipeline.close(wait=True)
        pipeline.store.close()
        return 0
    except (DINOv2Unavailable, MediaSourceError, OSError, RuntimeError, ValueError) as exc:
        print(f"❌ V0.2 乾跑失敗：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
