from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from meowkeeper.core.media import (
    ClipAssembler,
    DiskSegmentRingBuffer,
    EventClipRequest,
    EventClipService,
    FfmpegMediaIngest,
    SegmentRecorder,
    validate_media,
)
from meowkeeper.core.media.media_validation import MediaValidationResult
from meowkeeper.core.media.sources import BackoffPolicy, ResolvedStream
from meowkeeper.core.notifications.cat_preferences import CatNotificationPreferences
from meowkeeper.core.notifications.telegram import TelegramNotifier, TelegramSendResult
from meowkeeper.storage.database import SQLiteStore


class Phase2EventVideoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="meowkeeper-phase2-")
        self.root = Path(self.temporary.name)
        self.t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.ffmpeg = ROOT / "vendor" / "ffmpeg" / "bin" / "ffmpeg.exe"
        self.assertTrue(self.ffmpeg.is_file())

    def tearDown(self) -> None:
        self.doCleanups()
        self.temporary.cleanup()

    def test_network_ingest_reconnects_after_clean_eof(self) -> None:
        class FakeProcess:
            returncode = 0

            def __init__(self) -> None:
                self.stdout = io.BytesIO(b"\xff\xd8frame\xff\xd9")

            def poll(self) -> int:
                return 0

        launches: list[FakeProcess] = []

        def popen(*_args: object, **_kwargs: object) -> FakeProcess:
            process = FakeProcess()
            launches.append(process)
            return process

        source = "rtsp://127.0.0.1:28554/test"
        ring = DiskSegmentRingBuffer(self.root / "ring", retention_seconds=60)
        ingest = FfmpegMediaIngest(
            source, ring, ffmpeg_path=str(self.ffmpeg), popen=popen,
            sleep=lambda _seconds: None,
            retry_policy=BackoffPolicy(max_retries=2, initial_seconds=0, maximum_seconds=0),
        )
        frames = list(ingest.iter_frames(max_frames=2, resolved=ResolvedStream(source, source, "direct")))
        self.assertEqual(len(frames), 2)
        self.assertEqual(len(launches), 2)
        self.assertEqual(ingest.restart_count, 1)

    def test_network_ingest_keeps_reconnecting_after_default_retry_count(self) -> None:
        class FakeProcess:
            returncode = 0

            def __init__(self, has_frame: bool) -> None:
                self.stdout = io.BytesIO(b"\xff\xd8frame\xff\xd9" if has_frame else b"")

            def poll(self) -> int:
                return 0

        launches: list[FakeProcess] = []

        def popen(*_args: object, **_kwargs: object) -> FakeProcess:
            process = FakeProcess(len(launches) >= 4)
            launches.append(process)
            return process

        source = "rtsp://127.0.0.1:28554/test"
        ring = DiskSegmentRingBuffer(self.root / "ring", retention_seconds=60)
        ingest = FfmpegMediaIngest(source, ring, ffmpeg_path=str(self.ffmpeg), popen=popen, sleep=lambda _seconds: None)
        frames = list(ingest.iter_frames(max_frames=1, resolved=ResolvedStream(source, source, "direct")))
        self.assertEqual(len(frames), 1)
        self.assertEqual(len(launches), 5)
        self.assertEqual(ingest.restart_count, 4)

    def test_stopping_network_ingest_prevents_reconnect(self) -> None:
        class FakeProcess:
            returncode = 0

            def __init__(self) -> None:
                self.stdout = io.BytesIO(b"\xff\xd8frame\xff\xd9")

            def poll(self) -> int:
                return 0

        launches: list[FakeProcess] = []

        def popen(*_args: object, **_kwargs: object) -> FakeProcess:
            process = FakeProcess()
            launches.append(process)
            return process

        source = "rtsp://127.0.0.1:28554/test"
        ring = DiskSegmentRingBuffer(self.root / "ring", retention_seconds=60)
        ingest = FfmpegMediaIngest(source, ring, ffmpeg_path=str(self.ffmpeg), popen=popen, sleep=lambda _seconds: None)
        frames = ingest.iter_frames(resolved=ResolvedStream(source, source, "direct"))
        self.assertIsNotNone(next(frames))
        ingest.close()
        self.assertEqual(list(frames), [])
        self.assertEqual(len(launches), 1)

    def test_completed_youtube_video_does_not_reconnect(self) -> None:
        class FakeProcess:
            returncode = 0

            def __init__(self) -> None:
                self.stdout = io.BytesIO(b"\xff\xd8frame\xff\xd9")

            def poll(self) -> int:
                return 0

        class VODResolver:
            media_info = type("MediaInfo", (), {"is_live": False})()

        launches: list[FakeProcess] = []

        def popen(*_args: object, **_kwargs: object) -> FakeProcess:
            process = FakeProcess()
            launches.append(process)
            return process

        source = "https://www.youtube.com/watch?v=public-test"
        media_url = "https://video.example.test/stream"
        ring = DiskSegmentRingBuffer(self.root / "ring", retention_seconds=60)
        ingest = FfmpegMediaIngest(
            source, ring, resolver=VODResolver(), ffmpeg_path=str(self.ffmpeg),
            popen=popen, sleep=lambda _seconds: None,
        )
        frames = list(ingest.iter_frames(resolved=ResolvedStream(source, media_url, "yt-dlp")))
        self.assertEqual(len(frames), 1)
        self.assertEqual(len(launches), 1)
        self.assertEqual(ingest.restart_count, 0)

    def test_youtube_ingest_uses_one_chunked_upstream_and_one_ffmpeg_pipe(self) -> None:
        class FakeProcess:
            returncode = 0

            def __init__(self, payload: bytes) -> None:
                self.stdout = io.BytesIO(payload)

            def poll(self) -> int:
                return self.returncode

            def terminate(self) -> None:
                return None

            def kill(self) -> None:
                return None

            def wait(self, timeout: float | None = None) -> int:
                del timeout
                return self.returncode

        upstream = FakeProcess(b"media")

        class Resolver:
            media_info = type("MediaInfo", (), {"is_live": False})()
            starts = 0

            def start_download_process(self) -> FakeProcess:
                self.starts += 1
                return upstream

        resolver = Resolver()
        ffmpeg_commands: list[list[str]] = []

        def popen(command: list[str], **kwargs: object) -> FakeProcess:
            ffmpeg_commands.append(command)
            self.assertIs(kwargs["stdin"], upstream.stdout)
            return FakeProcess(b"\xff\xd8frame\xff\xd9")

        source = "https://www.youtube.com/watch?v=public-test"
        ring = DiskSegmentRingBuffer(self.root / "ring", retention_seconds=60)
        ingest = FfmpegMediaIngest(
            source,
            ring,
            resolver=resolver,
            ffmpeg_path=str(self.ffmpeg),
            popen=popen,
        )
        frames = list(
            ingest.iter_frames(
                max_frames=1,
                resolved=ResolvedStream(source, "https://signed.example/video", "yt-dlp"),
            )
        )
        self.assertEqual(len(frames), 1)
        self.assertEqual(resolver.starts, 1)
        self.assertEqual(len(ffmpeg_commands), 1)
        self.assertIn("pipe:0", ffmpeg_commands[0])
        self.assertNotIn("https://signed.example/video", ffmpeg_commands[0])
        self.assertTrue(upstream.stdout.closed)

    def test_youtube_vp9_uses_downsampled_h264_ring_transcode(self) -> None:
        class FakeProcess:
            returncode = 0

            def __init__(self, payload: bytes) -> None:
                self.stdout = io.BytesIO(payload)

            def poll(self) -> int:
                return self.returncode

            def terminate(self) -> None:
                return None

            def kill(self) -> None:
                return None

            def wait(self, timeout: float | None = None) -> int:
                del timeout
                return self.returncode

        upstream = FakeProcess(b"media")

        class Resolver:
            media_info = type("MediaInfo", (), {"is_live": False, "video_codec": "vp9"})()

            def start_download_process(self) -> FakeProcess:
                return upstream

        ffmpeg_commands: list[list[str]] = []

        def popen(command: list[str], **_kwargs: object) -> FakeProcess:
            ffmpeg_commands.append(command)
            return FakeProcess(b"\xff\xd8frame\xff\xd9")

        source = "https://www.youtube.com/watch?v=public-test"
        ring = DiskSegmentRingBuffer(self.root / "ring", retention_seconds=60)
        ingest = FfmpegMediaIngest(source, ring, resolver=Resolver(), ffmpeg_path=str(self.ffmpeg), popen=popen)
        frames = list(ingest.iter_frames(max_frames=1, resolved=ResolvedStream(source, "https://signed.example/video", "yt-dlp")))

        self.assertEqual(len(frames), 1)
        command = ffmpeg_commands[0]
        self.assertNotIn("copy", command)
        self.assertIn("libx264", command)
        self.assertIn("fps=5,scale=w='min(640,iw)':h=-2", command)
        self.assertEqual(command[command.index("-g") + 1], "5")
        self.assertEqual(command[command.index("-keyint_min") + 1], "5")
        self.assertEqual(ingest.last_error, "來源影片編碼不適用 MPEG-TS；事件快取改用 640 像素、最高 5 FPS 的低負載 H.264 轉碼。")

    def test_realtime_ring_uses_completed_file_time_and_skips_writer(self) -> None:
        ring = DiskSegmentRingBuffer(self.root / "ring", retention_seconds=60)
        session = ring.directory / "live_session"
        session.mkdir()
        ring.begin_session(session, realtime=True)
        now = datetime.now(timezone.utc)
        files: list[Path] = []
        for index, age in enumerate((9, 6, 3, 0)):
            path = session / f"segment_{index:08d}.ts"
            path.write_bytes(b"segment")
            timestamp = (now - timedelta(seconds=age)).timestamp()
            os.utime(path, (timestamp, timestamp))
            files.append(path)
            ring.scan()
        self.assertEqual([item.path for item in ring.index.all()], files[:3])
        self.assertIn(files[2], [item.path for item in ring.snapshot(5, now=now)])
        ring.end_session(session)
        self.assertEqual([item.path for item in ring.index.all()], files)

    def test_end_session_removes_only_its_zero_byte_media_segments(self) -> None:
        ring = DiskSegmentRingBuffer(self.root / "ring", retention_seconds=60)
        session = ring.directory / "ingest-empty-tail"
        session.mkdir(parents=True)
        valid = session / "segment_00000000.ts"
        empty_tail = session / "segment_00000001.ts"
        unrelated = ring.directory / "unrelated-empty.ts"
        valid.write_bytes(b"valid segment")
        empty_tail.write_bytes(b"")
        unrelated.write_bytes(b"")
        ring.begin_session(session)

        ring.end_session(session)

        self.assertFalse(empty_tail.exists())
        self.assertTrue(unrelated.exists())
        self.assertEqual([record.path for record in ring.index.all()], [valid.resolve()])

    def _source_ts(self, *, audio: bool = False) -> Path:
        source = self.root / ("audio.mp4" if audio else "cat.mp4")
        if audio:
            command = [
                str(self.ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=320x240:rate=10",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=880:sample_rate=44100",
                "-t",
                "2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(source),
            ]
        else:
            command = [
                str(self.ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(ROOT / "reports" / "cat_test.mp4"),
                "-c",
                "copy",
                "-f",
                "mpegts",
                str(source),
            ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return source

    def _populate_ring(self, count: int = 15, *, audio: bool = False) -> DiskSegmentRingBuffer:
        source = self._source_ts(audio=audio)
        ring = DiskSegmentRingBuffer(
            self.root / "ring",
            retention_seconds=60,
            segment_seconds=1,
            maximum_size_mb=64,
        )
        for index in range(count):
            destination = ring.directory / f"segment_{index:03d}.ts"
            shutil.copy2(source, destination)
            ring.register_file(
                destination,
                captured_at=self.t0 + timedelta(seconds=index),
                duration_seconds=1,
            )
        return ring

    def test_ring_rotates_to_retention_and_preserves_referenced_segment(self) -> None:
        ring = self._populate_ring(14)
        records = ring.index.all()
        self.assertLessEqual(ring.duration_seconds, 60)
        lease = ring.index.acquire([records[0]])
        ring.rotate(now=self.t0 + timedelta(seconds=200))
        self.assertTrue(records[0].path.exists())
        ring.release(lease)
        ring.rotate(now=self.t0 + timedelta(seconds=200))
        self.assertFalse(records[0].path.exists())

    def test_stream_copy_assembles_valid_mp4_and_live_returns_recent_clip(self) -> None:
        ring = self._populate_ring()
        assembler = ClipAssembler()
        output = self.root / "clip.mp4"
        result = assembler.assemble(ring.index.all(), output)
        self.assertEqual(result.status, "completed")
        self.assertTrue(result.used_stream_copy)
        self.assertTrue(validate_media(output).valid)
        service = EventClipService(
            ring,
            assembler,
            output_directory=self.root / "events",
            clock=lambda: self.t0 + timedelta(seconds=10),
            telegram_enabled=False,
        )
        live = service.get_recent_clip(5)
        service.close()
        self.assertEqual(live.status, "completed")
        self.assertIsNotNone(live.path)
        self.assertTrue(validate_media(live.path).valid)

    def test_event_pre_and_post_window_is_saved_to_sqlite_without_telegram(self) -> None:
        ring = self._populate_ring(20)
        store = SQLiteStore(self.root / "events.sqlite3")
        service = EventClipService(
            ring,
            ClipAssembler(),
            output_directory=self.root / "events",
            store=store,
            telegram_enabled=False,
            clock=lambda: self.t0 + timedelta(seconds=20),
        )
        accepted = service.submit_event(
            EventClipRequest(
                event_id="event-1",
                trigger_at=self.t0 + timedelta(seconds=10),
                event_type="cat_detected",
                source_id="local-test",
                confidence=0.91,
                pre_seconds=5,
                post_seconds=10,
                screenshot_bytes=b"not-used-after-success",
            )
        )
        self.assertTrue(accepted)
        self.assertTrue(service.wait_for_idle(20))
        row = store.get_media_event("event-1")
        service.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["clip_status"], "completed")
        self.assertEqual(row["telegram_status"], "disabled")
        self.assertGreater(row["actual_duration_seconds"], 0)
        self.assertTrue(Path(row["clip_path"]).is_file())

    def test_event_waits_for_long_gop_writer_to_finalize(self) -> None:
        ring = DiskSegmentRingBuffer(
            self.root / "delayed-ring",
            retention_seconds=60,
            segment_seconds=1,
            maximum_size_mb=64,
        )
        session = ring.directory / "ingest-delayed"
        session.mkdir(parents=True)
        ring.begin_session(session)
        shutil.copy2(self._source_ts(), session / "segment_00000000.ts")
        store = SQLiteStore(self.root / "delayed.sqlite3")
        service = EventClipService(
            ring,
            ClipAssembler(),
            output_directory=self.root / "delayed-events",
            store=store,
            telegram_enabled=False,
            segment_finalize_wait_seconds=2,
        )
        trigger = datetime.now(timezone.utc)
        self.assertTrue(
            service.submit_event(
                EventClipRequest(
                    "long-gop",
                    trigger,
                    "cat_detected",
                    "local-test",
                    pre_seconds=1,
                    post_seconds=0,
                    screenshot_bytes=b"fallback",
                )
            )
        )
        time.sleep(0.1)
        ring.end_session(session)
        self.assertTrue(service.wait_for_idle(10))
        row = store.get_media_event("long-gop")
        service.close()
        store.close()
        self.assertEqual(row["clip_status"], "completed")
        self.assertTrue(Path(row["clip_path"]).is_file())

    def test_final_long_gop_segment_covers_events_seen_while_writer_was_active(self) -> None:
        ring = DiskSegmentRingBuffer(
            self.root / "aged-long-gop-ring",
            retention_seconds=60,
            segment_seconds=1,
            maximum_size_mb=64,
        )
        session = ring.directory / "ingest-aged"
        session.mkdir(parents=True)
        ring.begin_session(session)
        trigger = datetime.now(timezone.utc)
        shutil.copy2(self._source_ts(), session / "segment_00000000.ts")
        ring._session_started_at[session] = trigger - timedelta(seconds=8)

        store = SQLiteStore(self.root / "aged-long-gop.sqlite3")
        service = EventClipService(
            ring,
            ClipAssembler(),
            output_directory=self.root / "aged-long-gop-events",
            store=store,
            telegram_enabled=False,
            segment_finalize_wait_seconds=2,
        )
        self.assertTrue(
            service.submit_event(
                EventClipRequest(
                    "aged-long-gop",
                    trigger - timedelta(seconds=4),
                    "cat_detected",
                    "local-test",
                    pre_seconds=1,
                    post_seconds=0,
                    screenshot_bytes=b"fallback",
                )
            )
        )
        time.sleep(0.1)
        ring.end_session(session)
        self.assertTrue(service.wait_for_idle(10))
        row = store.get_media_event("aged-long-gop")
        service.close()
        store.close()
        self.assertEqual(row["clip_status"], "completed")
        self.assertTrue(Path(row["clip_path"]).is_file())

    def test_same_event_type_is_cooled_down_without_second_clip(self) -> None:
        ring = self._populate_ring()
        service = EventClipService(
            ring,
            ClipAssembler(),
            output_directory=self.root / "events",
            clock=lambda: self.t0 + timedelta(seconds=10),
            telegram_enabled=False,
        )
        first = EventClipRequest("first", self.t0 + timedelta(seconds=10), "cat_detected", "source", pre_seconds=1, post_seconds=0)
        second = EventClipRequest("second", self.t0 + timedelta(seconds=11), "cat_detected", "source", pre_seconds=1, post_seconds=0)
        self.assertTrue(service.submit_event(first))
        self.assertFalse(service.submit_event(second))
        service.close()

    def test_low_disk_mode_rejects_ordinary_clip_but_keeps_event_metadata(self) -> None:
        ring = DiskSegmentRingBuffer(
            self.root / "ring",
            retention_seconds=60,
            maximum_size_mb=10,
            minimum_free_bytes=10**20,
        )
        store = SQLiteStore(self.root / "low-disk.sqlite3")
        service = EventClipService(
            ring,
            ClipAssembler(),
            output_directory=self.root / "events",
            store=store,
            clock=lambda: self.t0,
            telegram_enabled=False,
        )
        self.assertFalse(
            service.submit_event(
                EventClipRequest("low-disk", self.t0, "cat_detected", "source", pre_seconds=1, post_seconds=0)
            )
        )
        row = store.get_media_event("low-disk")
        service.close()
        self.assertEqual(row["clip_status"], "rejected_disk_low")

    def test_audio_stream_survives_segment_concat(self) -> None:
        ring = self._populate_ring(2, audio=True)
        result = ClipAssembler().assemble(ring.index.all(), self.root / "audio-clip.mp4")
        self.assertEqual(result.status, "completed")
        validation = validate_media(result.path)
        self.assertTrue(validation.valid)
        self.assertTrue(validation.has_audio)

    def test_telegram_transcode_sets_apple_playback_metadata_and_faststart(self) -> None:
        source = ROOT / "reports" / "cat_test.mp4"
        destination = self.root / "telegram-compatible.mp4"
        assembler = ClipAssembler()

        result = assembler.transcode_for_telegram(source, destination)

        self.assertEqual(result.status, "completed", result.error)
        self.assertIsNotNone(result.validation)
        self.assertTrue(result.validation.telegram_compatible)
        self.assertEqual(result.validation.video_codec, "h264")
        self.assertEqual(result.validation.pixel_format, "yuv420p")
        self.assertGreater(result.validation.width, 0)
        self.assertGreater(result.validation.height, 0)
        self.assertGreater(result.validation.duration_seconds, 0)
        decoded = subprocess.run(
            [str(self.ffmpeg), "-v", "error", "-i", str(destination), "-f", "null", "-"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(decoded.returncode, 0, decoded.stderr)

    def test_event_video_sends_dimensions_duration_and_streaming_hint(self) -> None:
        source = ROOT / "reports" / "cat_test.mp4"
        compatible = ClipAssembler().transcode_for_telegram(source, self.root / "compatible-event.mp4")
        self.assertEqual(compatible.status, "completed", compatible.error)
        source = compatible.path
        validation = validate_media(source)
        self.assertTrue(validation.telegram_compatible)
        notifier = Mock()
        notifier.send_video.return_value = TelegramSendResult("123", 77)
        service = EventClipService(
            Mock(),
            ClipAssembler(),
            output_directory=self.root / "events", notifier=notifier, chat_ids=["123"],
        )
        request = EventClipRequest("metadata-event", self.t0, "cat_detected", "source")
        try:
            attempts, error = service._send_with_retries(request, source, "測試事件")
        finally:
            service.close()
        self.assertEqual((attempts, error), (1, None))
        kwargs = notifier.send_video.call_args.kwargs
        self.assertEqual(kwargs["width"], validation.width)
        self.assertEqual(kwargs["height"], validation.height)
        self.assertAlmostEqual(kwargs["duration"], validation.duration_seconds)
        self.assertTrue(kwargs["supports_streaming"])

    def test_telegram_compatible_h264_is_sent_without_second_transcode(self) -> None:
        source = ROOT / "reports" / "cat_test.mp4"
        assembler = ClipAssembler()
        compatible = assembler.transcode_for_telegram(source, self.root / "already-compatible.mp4")
        self.assertEqual(compatible.status, "completed", compatible.error)
        self.assertIsNotNone(compatible.path)
        self.assertIsNotNone(compatible.validation)
        self.assertTrue(compatible.validation.telegram_compatible)

        transcode = unittest.mock.Mock(wraps=assembler.transcode_for_telegram)
        assembler.transcode_for_telegram = transcode  # type: ignore[method-assign]
        notifier = Mock()
        notifier.send_video.return_value = TelegramSendResult("123", 79)
        service = EventClipService(
            Mock(), assembler, output_directory=self.root / "events",
            notifier=notifier, chat_ids=["123"],
        )
        try:
            attempts, error = service._send_with_retries(
                EventClipRequest("direct-h264", self.t0, "cat_detected", "source"),
                compatible.path,
                "測試事件",
            )
        finally:
            service.close()
        self.assertEqual((attempts, error), (1, None))
        transcode.assert_not_called()
        self.assertEqual(notifier.send_video.call_args.args[1], compatible.path.read_bytes())

    def test_event_video_transcodes_incompatible_source_copy_before_send(self) -> None:
        source = ROOT / "reports" / "cat_test.mp4"
        original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        notifier = Mock()
        notifier.send_video.return_value = TelegramSendResult("123", 78)
        service = EventClipService(
            Mock(),
            ClipAssembler(),
            output_directory=self.root / "events", notifier=notifier, chat_ids=["123"],
        )
        try:
            attempts, error = service._send_with_retries(
                EventClipRequest("transcode-event", self.t0, "cat_detected", "source"),
                source,
                "測試事件",
            )
        finally:
            service.close()
        self.assertEqual((attempts, error), (1, None))
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original_hash)
        sent_path = self.root / "sent-copy.mp4"
        sent_path.write_bytes(notifier.send_video.call_args.args[1])
        sent_validation = validate_media(sent_path)
        self.assertTrue(sent_validation.telegram_compatible)
        kwargs = notifier.send_video.call_args.kwargs
        self.assertEqual((kwargs["width"], kwargs["height"]), (sent_validation.width, sent_validation.height))
        self.assertAlmostEqual(kwargs["duration"], sent_validation.duration_seconds)

    def test_stream_copy_failure_uses_transcode_fallback(self) -> None:
        ring = self._populate_ring(1)
        source = ROOT / "reports" / "cat_test.mp4"
        assembler = ClipAssembler()
        original = assembler._run_mode
        calls: list[bool] = []

        def run_mode(list_path: Path, destination: Path, *, stream_copy: bool) -> MediaValidationResult:
            calls.append(stream_copy)
            if stream_copy:
                return MediaValidationResult(destination, False, False, False, None, "copy failed")
            shutil.copy2(source, destination)
            return validate_media(destination)

        assembler._run_mode = run_mode  # type: ignore[method-assign]
        result = assembler.assemble(ring.index.all(), self.root / "fallback.mp4")
        assembler._run_mode = original  # type: ignore[method-assign]
        self.assertEqual(calls, [True, False])
        self.assertEqual(result.status, "completed")
        self.assertFalse(result.used_stream_copy)

    def test_rtsp_recorder_refuses_second_camera_connection(self) -> None:
        ring = DiskSegmentRingBuffer(self.root / "ring", retention_seconds=60, maximum_size_mb=10)
        recorder = SegmentRecorder("rtsp://admin:password@192.0.2.10:554/live", ring)
        with self.assertRaisesRegex(Exception, "第二條攝影機連線"):
            recorder.start()

    def test_ffmpeg_watchdog_restarts_after_forced_process_exit(self) -> None:
        class FakeProcess:
            def __init__(self, return_code: int) -> None:
                self.returncode = return_code

            def poll(self) -> int:
                return self.returncode

            def terminate(self) -> None:
                return None

            def wait(self, timeout: float | None = None) -> int:
                return self.returncode

            def kill(self) -> None:
                return None

        launches: list[FakeProcess] = []

        def popen(*_args: object, **_kwargs: object) -> FakeProcess:
            process = FakeProcess(-9 if len(launches) % 2 == 0 else 0)
            launches.append(process)
            return process

        ring = DiskSegmentRingBuffer(self.root / "ring", retention_seconds=60, maximum_size_mb=10)
        recorder = SegmentRecorder(
            "http://127.0.0.1:9/unavailable",
            ring,
            auto_restart=True,
            retry_policy=type("Policy", (), {"max_retries": 1, "delay_seconds": lambda _self, _n: 0.0})(),
            popen=popen,
            sleep=lambda _seconds: None,
            poll_interval_seconds=0.01,
        )
        recorder.start()
        deadline = time.monotonic() + 1.0
        while len(launches) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        recorder.stop()
        self.assertGreaterEqual(recorder.restart_count, 1)

    def test_telegram_disabled_never_calls_notifier(self) -> None:
        ring = self._populate_ring()
        notifier = Mock()
        service = EventClipService(
            ring,
            ClipAssembler(),
            output_directory=self.root / "events",
            notifier=notifier,
            chat_ids=["123"],
            telegram_enabled=False,
            clock=lambda: self.t0 + timedelta(seconds=10),
        )
        self.assertTrue(
            service.submit_event(
                EventClipRequest("event-disabled", self.t0 + timedelta(seconds=10), "cat_detected", "source", pre_seconds=1, post_seconds=0)
            )
        )
        self.assertTrue(service.wait_for_idle(20))
        service.close()
        notifier.send_video.assert_not_called()

    def test_cat_detected_and_finalized_triggers_do_not_duplicate_semantic_work(self) -> None:
        ring = self._populate_ring()
        store = SQLiteStore(self.root / "trigger-scope.sqlite3")
        self.addCleanup(store.close)
        semantic_service = Mock()
        service = EventClipService(
            ring,
            ClipAssembler(),
            output_directory=self.root / "trigger-scope-events",
            store=store,
            semantic_service=semantic_service,
            cooldown_seconds=0,
            merge_window_seconds=0,
            clock=lambda: self.t0 + timedelta(seconds=14),
            telegram_enabled=False,
        )
        try:
            self.assertTrue(service.submit_event(EventClipRequest(
                "cat-trigger", self.t0 + timedelta(seconds=10), "cat_detected", "local-test",
                pre_seconds=1, post_seconds=0, screenshot_bytes=b"synthetic-jpeg",
            )))
            self.assertTrue(service.submit_event(EventClipRequest(
                "finalized-trigger", self.t0 + timedelta(seconds=11), "finalized", "local-test",
                pre_seconds=1, post_seconds=0, screenshot_bytes=b"synthetic-jpeg",
            )))
            self.assertEqual(semantic_service.submit.call_count, 1)
            self.assertEqual(semantic_service.submit.call_args.args[0].event_id, "cat-trigger")
            self.assertTrue(service.wait_for_idle(20))
        finally:
            service.close()

    def test_telegram_video_is_sent_from_background_queue_after_local_clip(self) -> None:
        ring = self._populate_ring()
        store = SQLiteStore(self.root / "telegram.sqlite3")
        self.addCleanup(store.close)
        notifier = Mock()
        notifier.send_video.return_value = TelegramSendResult("123", 27)
        service = EventClipService(
            ring,
            ClipAssembler(),
            output_directory=self.root / "events",
            store=store,
            notifier=notifier,
            chat_ids=["123"],
            telegram_enabled=True,
            clock=lambda: self.t0 + timedelta(seconds=10),
            retry_sleep=lambda _seconds: None,
        )
        self.assertTrue(
            service.submit_event(
                EventClipRequest(
                    "event-video",
                    self.t0 + timedelta(seconds=10),
                    "cat_detected",
                    "source",
                    pre_seconds=1,
                    post_seconds=0,
                    screenshot_bytes=b"jpeg",
                )
            )
        )
        self.assertTrue(service.wait_for_idle(20))
        row = store.get_media_event("event-video")
        service.close()
        self.assertEqual(notifier.send_video.call_count, 1)
        self.assertEqual(row["telegram_status"], "sent")
        receipts = store.telegram_receipts("event-video")
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["message_id"], 27)
        self.assertEqual(receipts[0]["media_kind"], "video")
        self.assertEqual(len(receipts[0]["recipient_ref"]), 64)
        store.initialize()
        self.assertEqual(store.telegram_receipts("event-video")[0]["message_id"], 27)

    def test_telegram_video_uses_video_multipart_field(self) -> None:
        response = Mock()
        response.read.return_value = b'{"ok": true, "result": {"message_id": 9, "video": {"width": 320, "height": 240, "duration": 3}}}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with unittest.mock.patch("meowkeeper.core.notifications.telegram.urlopen", return_value=response) as opened:
            result = TelegramNotifier("123456:token").send_video(
                "123", b"mp4", caption="事件", width=320, height=240, duration=3, supports_streaming=True,
            )
        self.assertEqual(result.message_id, 9)
        self.assertEqual((result.width, result.height, result.duration), (320, 240, 3))
        body = opened.call_args.args[0].data.decode("latin1")
        self.assertIn("video", body)
        self.assertIn('name="width"', body)
        self.assertIn('name="height"', body)
        self.assertIn('name="duration"', body)
        self.assertIn('name="supports_streaming"', body)
        self.assertNotIn("123456:token", body)

    def test_personal_cooldown_keeps_both_local_clips_and_other_recipient_delivery(self) -> None:
        ring = self._populate_ring()
        store = SQLiteStore(self.root / "cooldown.sqlite3")
        self.addCleanup(store.close)
        notifier = Mock()
        notifier.send_video.return_value = TelegramSendResult("test-recipient", 101)
        preferences = CatNotificationPreferences(self.root / "cooldown.json")
        preferences.set_cooldown_minutes("123", 15)
        service = EventClipService(
            ring, ClipAssembler(), output_directory=self.root / "cooldown-events",
            store=store, notifier=notifier, chat_ids=["123", "456"], telegram_enabled=True,
            cat_notification_preferences=preferences, cooldown_seconds=0, merge_window_seconds=0,
            clock=lambda: self.t0 + timedelta(seconds=14),
        )
        try:
            for index in range(2):
                event_id = f"cd-cat-{index}"
                self.assertTrue(service.submit_event(EventClipRequest(
                    event_id, self.t0 + timedelta(seconds=10 + index), "cat_detected", "source",
                    pre_seconds=1, post_seconds=0, screenshot_bytes=b"jpeg",
                )))
                self.assertTrue(service.wait_for_idle(20))
                row = store.get_media_event(event_id)
                self.assertEqual(row["clip_status"], "completed")
                self.assertTrue(Path(row["clip_path"]).is_file())
            recipients = [call.args[0] for call in notifier.send_video.call_args_list]
            self.assertEqual(recipients.count("123"), 1)
            self.assertEqual(recipients.count("456"), 2)
            self.assertFalse(preferences.claim_delivery("123", "cat_detected"))
        finally:
            service.close()

    def test_muted_chat_keeps_local_cat_clip_without_sending(self) -> None:
        ring = self._populate_ring()
        store = SQLiteStore(self.root / "muted.sqlite3")
        self.addCleanup(store.close)
        notifier = Mock()
        preferences = CatNotificationPreferences(self.root / "cat-preferences.json")
        preferences.mute_hours("123", 2)
        service = EventClipService(
            ring, ClipAssembler(), output_directory=self.root / "muted-events",
            store=store, notifier=notifier, chat_ids=["123"], telegram_enabled=True,
            cat_notification_preferences=preferences,
            clock=lambda: self.t0 + timedelta(seconds=10),
        )
        self.assertTrue(service.submit_event(EventClipRequest(
            "muted-cat", self.t0 + timedelta(seconds=10), "cat_detected", "source",
            pre_seconds=1, post_seconds=0, screenshot_bytes=b"jpeg",
        )))
        self.assertTrue(service.wait_for_idle(20))
        row = store.get_media_event("muted-cat")
        service.close()
        self.assertEqual(row["clip_status"], "completed")
        self.assertTrue(Path(row["clip_path"]).is_file())
        self.assertEqual(row["telegram_status"], "muted")
        notifier.send_video.assert_not_called()
        notifier.send_photo.assert_not_called()


if __name__ == "__main__":
    unittest.main()
