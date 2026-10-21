from __future__ import annotations

import io
import subprocess
import sys
import unittest
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from meowkeeper.core.detection.frame_analysis import (
    AnalysisConfigurationError,
    FrameAnalysis,
    NoopFrameAnalyzer,
    YoloFrameAnalyzer,
)
from meowkeeper.core.events.analysis_pipeline import AnalysisPipeline
from meowkeeper.core.media.sources import (
    BackoffPolicy,
    FfmpegFrameSource,
    FramePacket,
    MediaSourceError,
    YouTubeStreamResolver,
    iter_jpeg_frames,
    resolve_source,
)
from meowkeeper.core.notifications.telegram import TelegramNotifier, TelegramSendResult
from meowkeeper.runtime import bundled_binary, default_model_path


def frame(sequence: int, seconds: int = 0) -> FramePacket:
    return FramePacket(
        sequence=sequence,
        captured_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds),
        source_url="https://www.youtube.com/watch?v=test",
        jpeg_bytes=b"\xff\xd8test\xff\xd9",
    )


class MediaSourceTests(unittest.TestCase):
    def test_ffmpeg_stderr_is_drained_and_bounded(self) -> None:
        process = Mock(stderr=io.BytesIO(b"decoder warning\n" * 10000))
        chunks: deque[bytes] = deque(maxlen=4)
        reader = FfmpegFrameSource._drain_process_stderr(process, chunks)
        reader.join(timeout=2)
        self.assertFalse(reader.is_alive())
        self.assertLessEqual(len(b"".join(chunks)), 4 * 4096)

    def test_bundled_ffmpeg_is_preferred_when_present(self) -> None:
        path = bundled_binary("ffmpeg")
        self.assertIsNotNone(path)
        self.assertTrue(Path(path).name.lower() == "ffmpeg.exe")

    def test_default_yolo_weight_is_present(self) -> None:
        path = default_model_path()
        self.assertIsNotNone(path)
        self.assertEqual(Path(path).name, "yolo11n.pt")

    def test_jpeg_pipe_is_split_into_frames(self) -> None:
        payload = b"noise\xff\xd8one\xff\xd9between\xff\xd8two\xff\xd9tail"
        self.assertEqual(list(iter_jpeg_frames(io.BytesIO(payload), chunk_size=3)), [
            b"\xff\xd8one\xff\xd9",
            b"\xff\xd8two\xff\xd9",
        ])

    def test_youtube_resolver_uses_streamlink_without_logging_url(self) -> None:
        completed = subprocess.CompletedProcess(
            ["streamlink"], 0, "https://signed.example/live?token=secret\n", ""
        )
        run = Mock(return_value=completed)
        resolver = YouTubeStreamResolver(
            streamlink_path="streamlink.exe", yt_dlp_path=None, run=run
        )
        resolved = resolver.resolve("https://www.youtube.com/watch?v=abc")
        self.assertEqual(resolved.resolver, "streamlink")
        self.assertEqual(resolved.safe_host, "signed.example")
        self.assertEqual(run.call_args.kwargs["shell"], False)

    def test_youtube_resolver_retries_with_bounded_backoff(self) -> None:
        results = iter(
            [
                subprocess.CompletedProcess(["streamlink"], 1, "", "temporary failure"),
                subprocess.CompletedProcess(["streamlink"], 0, "https://signed.example/live\n", ""),
            ]
        )
        run = Mock(side_effect=lambda *args, **kwargs: next(results))
        sleeps: list[float] = []
        resolver = YouTubeStreamResolver(
            streamlink_path="streamlink.exe",
            yt_dlp_path=None,
            run=run,
            retry_policy=BackoffPolicy(max_retries=1, initial_seconds=0.01, maximum_seconds=0.01),
            sleep=sleeps.append,
        )
        resolved = resolver.resolve("https://www.youtube.com/watch?v=abc")
        self.assertEqual(resolved.resolver, "streamlink")
        self.assertEqual(run.call_count, 2)
        self.assertEqual(sleeps, [0.01])

    def test_missing_youtube_resolver_is_actionable(self) -> None:
        resolver = YouTubeStreamResolver(streamlink_path=None, yt_dlp_path=None)
        with patch("meowkeeper.core.media.sources.installed_script", return_value=None):
            resolver.streamlink_path = None
            resolver.yt_dlp_path = None
            with self.assertRaises(MediaSourceError):
                resolver.resolve("https://www.youtube.com/watch?v=abc")

    def test_rtsp_command_uses_tcp_and_does_not_use_onvif(self) -> None:
        source = FfmpegFrameSource(
            "rtsp://admin:secret@192.0.2.10:554/path", ffmpeg_path="ffmpeg.exe"
        )
        command = source.build_command("rtsp://admin:secret@192.0.2.10:554/path")
        limited = source.build_command("rtsp://admin:secret@192.0.2.10:554/path", max_frames=1)
        self.assertEqual(limited[-3:], ["-frames:v", "1", "pipe:1"])
        self.assertIn("-rtsp_transport", command)
        self.assertIn("tcp", command)
        self.assertNotIn("onvif", " ".join(command).lower())

    def test_local_direct_source_is_not_resolved_as_youtube(self) -> None:
        result = resolve_source("rtsp://192.0.2.10:554/path")
        self.assertEqual(result.resolver, "direct")
        self.assertEqual(result.stream_url, "rtsp://192.0.2.10:554/path")


class AnalysisAndNotificationTests(unittest.TestCase):
    def test_noop_analyzer_does_not_trigger(self) -> None:
        result = NoopFrameAnalyzer().analyze(frame(1))
        self.assertFalse(result.triggered)
        self.assertIsNone(result.event_type)

    def test_yolo_missing_model_never_downloads(self) -> None:
        with self.assertRaisesRegex(AnalysisConfigurationError, "模型檔不存在"):
            YoloFrameAnalyzer(str(ROOT / "models" / "missing.pt"))

    def test_pipeline_cooldown_sends_only_once(self) -> None:
        analyzer = Mock()
        analyzer.analyze.return_value = FrameAnalysis(
            True, "cat_detected", 0.91, "偵測到貓咪。"
        )
        notifier = Mock()
        notifier.send_photo.side_effect = lambda chat_id, image, caption: TelegramSendResult(
            chat_id, 1
        )
        pipeline = AnalysisPipeline(
            analyzer, notifier=notifier, chat_ids=["1", "2"], cooldown_seconds=180
        )
        events = pipeline.run([frame(1), frame(2, seconds=10)])
        self.assertEqual(len(events), 1)
        self.assertEqual(notifier.send_photo.call_count, 2)

    @patch("meowkeeper.core.notifications.telegram.urlopen")
    def test_telegram_photo_uses_multipart_without_exposing_token(self, urlopen: Mock) -> None:
        response = Mock()
        response.read.return_value = b'{"ok": true, "result": {"message_id": 7}}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        urlopen.return_value = response
        token = "123456:secret-token"
        result = TelegramNotifier(token).send_photo("123", b"jpeg", caption="事件")
        self.assertEqual(result.message_id, 7)
        request = urlopen.call_args.args[0]
        self.assertNotIn(token, request.data.decode("latin1"))
        self.assertIn("photo", request.data.decode("latin1"))


if __name__ == "__main__":
    unittest.main()
