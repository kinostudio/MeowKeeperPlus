import subprocess
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from meowkeeper.core.media.annotated_export import annotation_filters
from meowkeeper.core.media.clip_assembler import ClipAssembler
from meowkeeper.runtime import bundled_binary


class AnnotatedExportTests(unittest.TestCase):
    def test_failed_temp_setup_or_bad_worker_result_does_not_stick_export_lock(self):
        from fastapi.testclient import TestClient

        from meowkeeper.app.web.api import create_app
        from meowkeeper.app.web.auth import ApiAuthenticator
        from meowkeeper.app.web.state import CoreStateFacade

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.mp4"
            path.write_bytes(b"fixture")
            core = CoreStateFacade()
            core.event_file = lambda _event: path
            auth = ApiAuthenticator(token="a" * 32, csrf_token="b" * 32)
            headers = {"Authorization": "Bearer " + "a" * 32, "X-MeowKeeper-CSRF": "b" * 32}
            with TestClient(create_app(facade=core, authenticator=auth)) as client, \
                    patch("psutil.virtual_memory", return_value=SimpleNamespace(available=4 * 1024**3)):
                with patch("meowkeeper.app.web.api.tempfile.TemporaryDirectory", side_effect=OSError("not-for-user-log")):
                    response = client.post("/api/v1/events/test/annotated-video", headers=headers)
                    self.assertEqual(response.status_code, 503)
                    self.assertNotIn("not-for-user-log", response.text)
                def malformed_worker(command, **_kwargs):
                    Path(command[4]).write_bytes(b"fixture-output")
                    return subprocess.CompletedProcess(command, 0, stdout="not-json", stderr="not-for-user-log")
                with patch("subprocess.run", side_effect=malformed_worker):
                    response = client.post("/api/v1/events/test/annotated-video", headers=headers)
                    self.assertEqual(response.status_code, 503, "lock must release after temp setup fails")
                    self.assertNotIn("not-for-user-log", response.text)
                    second = client.post("/api/v1/events/test/annotated-video", headers=headers)
                    self.assertEqual(second.status_code, 503, "lock must release after invalid worker JSON")

    def test_export_requires_login_and_csrf(self):
        from fastapi.testclient import TestClient

        from meowkeeper.app.web.api import create_app
        from meowkeeper.app.web.auth import ApiAuthenticator

        auth = ApiAuthenticator(token="a" * 32, csrf_token="b" * 32)
        with TestClient(create_app(authenticator=auth)) as client:
            self.assertEqual(client.post("/api/v1/events/not-real/annotated-video").status_code, 401)
            self.assertEqual(client.post("/api/v1/events/not-real/annotated-video", headers={"Authorization": "Bearer " + "a" * 32}).status_code, 403)
            self.assertEqual(client.post("/api/v1/events/not-real/annotated-video", headers={"Authorization": "Bearer " + "a" * 32, "X-MeowKeeper-CSRF": "b" * 32}).status_code, 404)

    def test_real_filter_encoding_keeps_duration_and_draws_only_in_evidence_window(self):
        binary = bundled_binary("ffmpeg")
        if not binary:
            self.skipTest("缺少內附 FFmpeg")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "source.mp4", root / "marked.mp4"
            result = subprocess.run([binary, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                                     "-i", "color=c=blue:size=640x360:rate=20", "-t", "2",
                                     "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
                                    capture_output=True, check=False, timeout=20, shell=False)
            self.assertEqual(result.returncode, 0)
            original = source.read_bytes()
            filters = root / "filters.txt"
            filters.write_text(annotation_filters([(0.0, [[.1, .2, .4, .5]])]), encoding="ascii")
            encoded = ClipAssembler(timeout_seconds=20).transcode_for_telegram(source, output, annotation_filter_file=filters)
            self.assertIsNotNone(encoded.path, encoded.error)
            self.assertTrue(encoded.validation.telegram_compatible)
            self.assertAlmostEqual(encoded.actual_duration_seconds, 2, delta=.1)
            self.assertEqual(source.read_bytes(), original)
            for second, orange in ((.1, True), (1.1, False)):
                frame = subprocess.run([binary, "-hide_banner", "-loglevel", "error", "-ss", str(second),
                                        "-i", str(output), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
                                       capture_output=True, check=False, timeout=15, shell=False)
                self.assertEqual(frame.returncode, 0)
                with Image.open(BytesIO(frame.stdout)) as image:
                    r, g, b = image.convert("RGB").getpixel((65, 85))
                    self.assertEqual(r > 180 and g > 70 and b < 80, orange)
