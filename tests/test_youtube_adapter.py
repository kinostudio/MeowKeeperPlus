from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from meowkeeper.core.media.youtube import (
    YOUTUBE_RECONNECT_BACKOFF_SECONDS,
    YouTubeSourceAdapter,
    YouTubeSourceConfig,
    YouTubeSourceStatus,
    YouTubeUnsupportedError,
)


def _metadata(url: str = "https://signed.example/video") -> str:
    return json.dumps(
        {
            "id": "video-id",
            "title": "公開測試影片",
            "is_live": False,
            "live_status": "not_live",
            "height": 480,
            "vcodec": "avc1.4d401f",
            "acodec": "none",
            "url": url,
        }
    )


class _FakeProcess:
    def __init__(self, payload: bytes, returncode: int) -> None:
        self.stdout = io.BytesIO(payload)
        self.stderr = io.BytesIO(b"")
        self.returncode = returncode

    def poll(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode


class YouTubeAdapterTests(unittest.TestCase):
    def test_config_only_contains_original_page_url(self) -> None:
        config = YouTubeSourceConfig("https://youtu.be/abc", max_height=360)
        saved = config.to_config()
        self.assertEqual(saved["url"], "https://youtu.be/abc")
        self.assertNotIn("signed", json.dumps(saved))

    def test_resolve_uses_yt_dlp_metadata_and_masks_media_url(self) -> None:
        run = Mock(
            return_value=subprocess.CompletedProcess(
                ["yt-dlp"], 0, _metadata(), ""
            )
        )
        adapter = YouTubeSourceAdapter(
            YouTubeSourceConfig("https://www.youtube.com/watch?v=abc", max_height=480),
            yt_dlp_path="yt-dlp.exe",
            ffmpeg_path="ffmpeg.exe",
            run=run,
        )
        resolved = adapter.resolve()
        self.assertEqual(resolved.resolver, "yt-dlp")
        self.assertEqual(adapter.media_info.video_id, "video-id")
        self.assertEqual(adapter.media_info.selected_height, 480)
        self.assertEqual(adapter.media_info.video_codec, "avc1.4d401f")
        self.assertNotIn("signed.example", json.dumps(adapter.status_snapshot()))
        command = run.call_args.args[0]
        self.assertEqual(command[0], "yt-dlp.exe")
        self.assertIn("--dump-single-json", command)
        self.assertIn("bestvideo[height<=480]/best[height<=480]", command)
        self.assertNotIn("streamlink", " ".join(command))

    def test_private_source_is_explicitly_unsupported(self) -> None:
        run = Mock(
            return_value=subprocess.CompletedProcess(
                ["yt-dlp"], 1, "", "ERROR: Sign in to confirm your age"
            )
        )
        adapter = YouTubeSourceAdapter(
            YouTubeSourceConfig("https://www.youtube.com/watch?v=abc"),
            yt_dlp_path="yt-dlp.exe",
            ffmpeg_path="ffmpeg.exe",
            run=run,
        )
        with self.assertRaises(YouTubeUnsupportedError):
            adapter.resolve()
        self.assertEqual(adapter.status, YouTubeSourceStatus.UNSUPPORTED.value)

    def test_ffmpeg_exit_reruns_ytdlp_with_bounded_backoff(self) -> None:
        run = Mock(
            side_effect=[
                subprocess.CompletedProcess(["yt-dlp"], 0, _metadata(), ""),
                subprocess.CompletedProcess(["yt-dlp"], 0, _metadata("https://signed.example/video2"), ""),
            ]
        )
        processes = iter(
            [
                _FakeProcess(b"media", 0),
                _FakeProcess(b"", 1),
                _FakeProcess(b"media", 0),
                _FakeProcess(b"\xff\xd8frame\xff\xd9", 0),
            ]
        )
        adapter = YouTubeSourceAdapter(
            YouTubeSourceConfig("https://www.youtube.com/watch?v=abc"),
            yt_dlp_path="yt-dlp.exe",
            ffmpeg_path="ffmpeg.exe",
            run=run,
            popen=Mock(side_effect=lambda *args, **kwargs: next(processes)),
        )
        sleeps: list[float] = []
        adapter._wait_before_retry = lambda index: sleeps.append(YOUTUBE_RECONNECT_BACKOFF_SECONDS[index])
        frames = list(adapter.iter_frames(max_frames=1))
        self.assertEqual(len(frames), 1)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(sleeps, [3.0])
        self.assertGreaterEqual(adapter.reconnect_count, 1)

    def test_downloader_pipe_uses_finite_chunks_and_original_url_only(self) -> None:
        adapter = YouTubeSourceAdapter(
            YouTubeSourceConfig("https://www.youtube.com/watch?v=abc", max_height=720),
            yt_dlp_path="yt-dlp.exe",
            ffmpeg_path="ffmpeg.exe",
            js_runtime_path="node.exe",
        )
        command = adapter.build_download_command()
        self.assertEqual(command[0], "yt-dlp.exe")
        self.assertIn("--http-chunk-size", command)
        self.assertIn("1M", command)
        self.assertEqual(command[-1], "https://www.youtube.com/watch?v=abc")
        self.assertNotIn("signed.example", " ".join(command))

    def test_same_source_has_one_producer_lease(self) -> None:
        config = YouTubeSourceConfig("https://www.youtube.com/watch?v=abc")
        first = YouTubeSourceAdapter(config, yt_dlp_path="yt-dlp.exe", ffmpeg_path="ffmpeg.exe")
        second = YouTubeSourceAdapter(config, yt_dlp_path="yt-dlp.exe", ffmpeg_path="ffmpeg.exe")
        self.assertTrue(first._acquire("producer"))
        try:
            self.assertFalse(second._acquire("producer"))
        finally:
            first._release("producer")


if __name__ == "__main__":
    unittest.main()
