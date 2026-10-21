import json
import os
import subprocess
import threading
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from unittest.mock import patch

from PIL import Image

from meowkeeper.core.event_semantics import EventSemanticService, SemanticRequest
from meowkeeper.core.semantic_storyboard import FrameEvidence, FrameEvidenceBuffer
from meowkeeper.integrations.vision.openai_compatible import OpenAICompatibleVision, VisionEndpoint
from meowkeeper.integrations.vision.transport import ProviderError, request_json


class VisionTransportTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.status = 200
        self.delay = 0
        self.redirect = False
        self.retry_after = None
        self.response = {"choices": [{"message": {"content": '{"tracks":[{"track_id":"scene","tags":[]}]}'}}]}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                owner.calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                time.sleep(owner.delay)
                try:
                    self.send_response(302 if owner.redirect else owner.status)
                    if owner.redirect:
                        self.send_header("Location", owner.endpoint)
                    if owner.retry_after is not None:
                        self.send_header("Retry-After", owner.retry_after)
                    self.end_headers()
                    self.wfile.write(json.dumps(owner.response).encode())
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}/v1/chat/completions"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def provider(self, **kwargs):
        values = {"endpoint": self.endpoint, "model_id": "test-model", "enabled": True, "upload_consent": True}
        values.update(kwargs)
        return OpenAICompatibleVision(VisionEndpoint(**values), lambda: None)

    def test_real_local_http_child_adapter_and_no_private_context(self):
        result = self.provider(json_object_supported=True).analyze(SemanticRequest("private-event", b"synthetic-jpeg"), timeout_seconds=3)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result.request_dispatched)
        self.assertEqual(result.http_status, 200)
        self.assertGreaterEqual(result.api_elapsed_ms, 0)
        self.assertNotIn("private-event", json.dumps(self.calls))
        self.assertEqual(self.calls[0]["response_format"], {"type": "json_object"})
        self.assertEqual(self.calls[0]["messages"][1]["content"][1]["type"], "image_url")

    def test_credentials_are_not_in_process_arguments_or_environment(self):
        key = "TEST_ONLY_KEY_NOT_A_REAL_CREDENTIAL"
        original = subprocess.Popen
        starts = []

        def capture(args, **kwargs):
            starts.append((args, kwargs["env"]))
            return original(args, **kwargs)
        provider = OpenAICompatibleVision(
            VisionEndpoint(self.endpoint, "test-model", enabled=True, upload_consent=True), lambda: key)
        with patch.dict(os.environ, {"MEOWKEEPER_VISION_API_KEY": key}), \
                patch("meowkeeper.integrations.vision.transport.subprocess.Popen", side_effect=capture):
            provider.analyze(SemanticRequest("e", b"jpeg"), timeout_seconds=3)
        self.assertEqual(len(starts), 1)
        self.assertNotIn(key, str(starts))
        self.assertNotIn("MEOWKEEPER_VISION_API_KEY", starts[0][1])

    def test_disabled_no_consent_and_unmapped_track_never_make_request(self):
        for provider, request, expected in [
            (self.provider(enabled=False), SemanticRequest("e", b"jpeg"), "skipped_disabled"),
            (self.provider(upload_consent=False), SemanticRequest("e", b"jpeg"), "skipped_no_consent"),
            (self.provider(), SemanticRequest("e", b"jpeg", ("t",)), "insufficient_evidence"),
        ]:
            with self.assertRaises(ProviderError) as caught:
                provider.analyze(request, timeout_seconds=2)
            self.assertEqual(caught.exception.category, expected)
        self.assertEqual(self.calls, [])

    def test_http_errors_are_safe_categories_not_body_or_key(self):
        self.response = {"error": "credential-must-never-be-exposed"}
        for status, category in [(401, "auth_error"), (403, "auth_error"), (429, "rate_limited"),
                                 (500, "unavailable"), (400, "bad_response")]:
            self.status = status
            with self.assertRaises(ProviderError) as caught:
                request_json(self.endpoint, {}, {}, timeout_seconds=3)
            self.assertEqual(caught.exception.category, category)
            self.assertEqual(caught.exception.http_status, status)
            self.assertTrue(caught.exception.request_dispatched)
            self.assertGreaterEqual(caught.exception.api_elapsed_ms, 0)
            self.assertNotIn("credential", str(caught.exception))

    def test_hard_deadline_kills_only_owned_http_child(self):
        self.delay = 2
        started = time.monotonic()
        with self.assertRaises(ProviderError) as caught:
            request_json(self.endpoint, {}, {}, timeout_seconds=.3)
        self.assertEqual(caught.exception.category, "timeout")
        if self.calls:  # Windows子程序若尚未啟動，不能憑timeout推定已送出。
            self.assertTrue(caught.exception.request_dispatched)
        else:
            self.assertIn(caught.exception.request_dispatched, (None, True))
        self.assertLess(time.monotonic() - started, 1.5)

    def test_http_retry_after_is_bounded_safe_metadata(self):
        self.status = 429
        self.retry_after = "120"
        with self.assertRaises(ProviderError) as caught:
            request_json(self.endpoint, {}, {}, timeout_seconds=3)
        self.assertEqual(caught.exception.retry_after_seconds, 120)
        self.retry_after = "not-a-date-private-value"
        with self.assertRaises(ProviderError) as caught:
            request_json(self.endpoint, {}, {}, timeout_seconds=3)
        self.assertIsNone(caught.exception.retry_after_seconds)
        self.assertNotIn("private-value", str(caught.exception))

    def test_redirect_and_malformed_results_fail_closed(self):
        self.redirect = True
        with self.assertRaises(ProviderError):
            request_json(self.endpoint, {}, {}, timeout_seconds=3)
        self.assertEqual(len(self.calls), 1)
        self.redirect = False
        self.response = {"choices": []}
        with self.assertRaises(ProviderError) as caught:
            self.provider().analyze(SemanticRequest("e", b"jpeg"), timeout_seconds=3)
        self.assertEqual(caught.exception.category, "bad_response")

    def test_single_image_does_not_become_temporal_action_evidence(self):
        self.response = {"choices": [{"message": {"content": '{"tracks":[{"track_id":"scene","tags":["walking"]}]}'}}]}
        service = EventSemanticService(self.provider())
        try:
            result = service.submit(SemanticRequest("e", b"jpeg")).result(3)
            self.assertEqual(result.status, "insufficient_evidence")
            self.assertEqual(result.tracks, ())
        finally:
            service.close()

    def test_two_tracks_temporal_input_real_local_http_and_sanitized_evidence(self):
        trigger = datetime.now(timezone.utc)
        buffer = FrameEvidenceBuffer()
        tracks = ("private-track-a", "private-track-b")
        for track in tracks:
            for index in range(4):
                output = BytesIO()
                Image.new("RGB", (96, 96), (40 + index * 30, 80, 90)).save(output, format="JPEG")
                buffer.observe((FrameEvidence(track, "epoch", trigger + timedelta(seconds=.3 * index),
                                              output.getvalue()),))
        self.response = {"choices": [{"message": {"content": json.dumps({"tracks": [
            {"track_alias": alias, "tags": [{"code": code, "certainty": "low", "evidence_frame_indices": [0, 2]}]}
            for alias, code in (("track_01", "walking"), ("track_02", "grooming"))
        ], "summary": "不保存的自由文字"})}}]}
        service = EventSemanticService(self.provider(), input_preparer=lambda request: replace(
            request, storyboards=buffer.collect(request.track_ids, request.trigger_at, wait_seconds=0)))
        try:
            result = service.submit(SemanticRequest("private-event", b"jpeg", tracks, trigger)).result(3)
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.tracks, ((tracks[0], ("walking",)), (tracks[1], ("grooming",))))
            self.assertEqual(len(result.evidence), 2)
            self.assertIn("preparation_ms", result.payload()["timings"])
            self.assertNotIn("自由文字", str(result.payload()))
            self.assertNotIn("private-track", json.dumps(self.calls))
            self.assertNotIn("private-event", json.dumps(self.calls))
        finally:
            service.close()
            buffer.close()

    def test_endpoint_rejects_insecure_remote_credentials_query_and_fragment(self):
        for endpoint in ("http://example.com/v1", "https://user:pass@example.com/v1",
                         "https://example.com/v1?key=x", "https://example.com/v1#secret"):
            with self.assertRaises(ValueError):
                VisionEndpoint(endpoint, "test")


if __name__ == "__main__":
    unittest.main()
