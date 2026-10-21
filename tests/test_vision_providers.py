"""兩種協定共用安全結果契約；不呼叫真實付費供應商。"""

import base64
import json
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from meowkeeper.core.event_semantics import ProviderReply, SemanticRequest, normalize_result
from meowkeeper.core.semantic_storyboard import TrackStoryboard
from meowkeeper.integrations.vision.gemini import GeminiVision
from meowkeeper.integrations.vision.openai_compatible import OpenAICompatibleVision, VisionEndpoint
from meowkeeper.integrations.vision.providers import capabilities, health_check
from meowkeeper.integrations.vision.transport import ProviderError, request_json


class VisionProviderTests(unittest.TestCase):
    def request(self):
        board = TrackStoryboard("track", "track_01", b"fixture-crop", (-1,-.5,0,.5), "epoch")
        return SemanticRequest("event", b"fixture", ("track",), datetime.now(timezone.utc), (board,))

    def test_gemini_uses_inline_images_and_returns_only_verified_candidates(self):
        settings = VisionEndpoint("https://generativelanguage.googleapis.com/v1beta/models/fixture:generateContent", "fixture", True, True, True)
        provider = GeminiVision(settings, lambda:"fixture-key")
        raw = {"tracks":[{"track_alias":"track_01", "tags":[{"code":"sitting","certainty":"medium","evidence_frame_indices":[0,1]}]}], "secret":"must-not-be-stored"}
        reply = ProviderReply({"candidates":[{"content":{"parts":[{"text":json.dumps(raw)}]}}]}, request_dispatched=True, http_status=200, api_elapsed_ms=12)
        with patch("meowkeeper.integrations.vision.gemini.request_json", return_value=reply) as send:
            result = provider.analyze(self.request(), timeout_seconds=2)
        call = send.call_args
        self.assertEqual(call.args[1]["x-goog-api-key"],"fixture-key")
        self.assertNotIn("Authorization",call.args[1])
        self.assertIn("inlineData",call.args[2]["contents"][0]["parts"][1])
        self.assertEqual(call.args[2]["generationConfig"]["responseMimeType"],"application/json")
        self.assertEqual(normalize_result(result,self.request()).tracks,(("track",("sitting",)),))
        self.assertNotIn("must-not-be-stored",str(result))
        self.assertTrue(result.request_dispatched)

    def test_gemini_count_tokens_uses_same_synthetic_generate_body(self):
        settings = VisionEndpoint("https://generativelanguage.googleapis.com/v1beta/models/fixture:generateContent",
                                  "fixture", True, True, True)
        provider = GeminiVision(settings, lambda: "fixture-key")
        request = self.request()
        reply = ProviderReply({"totalTokens": 321}, request_dispatched=True, http_status=200, api_elapsed_ms=14)
        with patch("meowkeeper.integrations.vision.gemini.request_json", return_value=reply) as send:
            result = provider.count_tokens(request, timeout_seconds=3)
        self.assertEqual(result["totalTokens"], 321)
        self.assertEqual(send.call_args.args[0], "https://generativelanguage.googleapis.com/v1beta/models/fixture:countTokens")
        self.assertEqual(send.call_args.args[1], {"Content-Type": "application/json", "x-goog-api-key": "fixture-key"})
        self.assertEqual(send.call_args.args[2], {"generateContentRequest": provider._generate_body(request)})
        self.assertTrue(send.call_args.args[2]["generateContentRequest"]["contents"][0]["parts"][1]["inlineData"]["data"])

    def test_scene_reference_is_a_separate_context_image_for_gemini(self):
        settings = VisionEndpoint("https://generativelanguage.googleapis.com/v1beta/models/fixture:generateContent",
                                  "fixture", True, True, True)
        provider = GeminiVision(settings, lambda: "fixture-key")
        request = replace(self.request(), scene_reference_jpeg=b"local-scene-collage",
                          scene_reference_labels=("Reference 01: 食物／飯碗; view: 客廳; user label: 飯碗",))
        reply = ProviderReply({"candidates":[{"content":{"parts":[{"text":
            '{"tracks":[{"track_alias":"track_01","tags":[]}]}'}]}}]},
            request_dispatched=True, http_status=200)
        with patch("meowkeeper.integrations.vision.gemini.request_json", return_value=reply) as send:
            provider.analyze(request, timeout_seconds=2)
        parts = send.call_args.args[2]["contents"][0]["parts"]
        self.assertEqual(len(parts), 4)
        self.assertIn("飯碗", parts[2]["text"])
        self.assertEqual(parts[3]["inlineData"]["data"],
                         base64.b64encode(b"local-scene-collage").decode("ascii"))
        self.assertIn("不是貓咪身分或行為證據", send.call_args.args[2]["systemInstruction"]["parts"][0]["text"])

    def test_openai_compatible_image_detail_setting_is_applied_to_each_storyboard(self):
        settings = VisionEndpoint("https://example.invalid/v1/chat/completions", "fixture", True, True,
                                  image_detail="high")
        provider = OpenAICompatibleVision(settings, lambda: None)
        reply = ProviderReply({"choices": [{"message": {"content": '{"tracks":[{"track_alias":"track_01","tags":[]}]}'} }]},
                              request_dispatched=True, http_status=200, api_elapsed_ms=10)
        with patch("meowkeeper.integrations.vision.openai_compatible.request_json", return_value=reply) as send:
            provider.analyze(self.request(), timeout_seconds=2)
        content = send.call_args.args[2]["messages"][1]["content"]
        self.assertEqual(content[1]["image_url"]["detail"], "high")

    def test_scene_reference_is_a_separate_context_image_for_openai_compatible(self):
        settings = VisionEndpoint("https://example.invalid/v1/chat/completions", "fixture", True, True)
        provider = OpenAICompatibleVision(settings, lambda: None)
        request = replace(self.request(), scene_reference_jpeg=b"local-scene-collage",
                          scene_reference_labels=("Reference 01: 飲水／水盆; view: 客廳; user label: 水盆",))
        reply = ProviderReply({"choices":[{"message":{"content":
            '{"tracks":[{"track_alias":"track_01","tags":[]}]}'} }]},
            request_dispatched=True, http_status=200)
        with patch("meowkeeper.integrations.vision.openai_compatible.request_json", return_value=reply) as send:
            provider.analyze(request, timeout_seconds=2)
        content = send.call_args.args[2]["messages"][1]["content"]
        self.assertEqual(len(content), 4)
        self.assertIn("水盆", content[2]["text"])
        self.assertTrue(content[3]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertIn("不是事件當下畫面", send.call_args.args[2]["messages"][0]["content"])

    def test_gemini_disabled_invalid_alias_and_wrong_endpoint_do_not_bypass_contract(self):
        settings = VisionEndpoint("https://generativelanguage.googleapis.com/v1beta/models/fixture:generateContent","fixture")
        with patch("meowkeeper.integrations.vision.gemini.request_json") as send, self.assertRaises(ProviderError):
            GeminiVision(settings,lambda:None).analyze(self.request(),timeout_seconds=2)
        send.assert_not_called()
        with self.assertRaises(ValueError):
            GeminiVision(VisionEndpoint("https://example.invalid/other","fixture"),lambda:None)
        enabled = VisionEndpoint(settings.endpoint,"fixture",True,True)
        reply = {"candidates":[{"content":{"parts":[{"text":'{"tracks":[{"track_alias":"invented","tags":[]}]}'}]}}]}
        with patch("meowkeeper.integrations.vision.gemini.request_json",return_value=reply), self.assertRaises(ProviderError):
            GeminiVision(enabled,lambda:None).analyze(self.request(),timeout_seconds=2)

    def test_health_check_is_metadata_get_not_image_inference(self):
        settings = VisionEndpoint("https://example.invalid/v1/chat/completions","fixture")
        with patch("meowkeeper.integrations.vision.providers.request_json",return_value=ProviderReply({"data":[{"id":"fixture"}]},http_status=200)) as send:
            result = health_check("openai_compatible",settings,"fixture-key")
        self.assertEqual(result["status"],"metadata_available")
        self.assertEqual(send.call_args.args[0],"https://example.invalid/v1/models")
        self.assertEqual(send.call_args.args[2],{})
        self.assertEqual(send.call_args.kwargs["method"],"GET")
        self.assertFalse(capabilities("gemini")["video"])
        self.assertTrue(capabilities("gemini")["token_usage"])

    def test_gemini_model_discovery_uses_generation_capability_and_safe_ids(self):
        settings = VisionEndpoint("https://generativelanguage.googleapis.com/v1beta/models/fixture:generateContent", "fixture")
        reply = ProviderReply({"models": [
            {"name": "models/fixture", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/embed-only", "supportedGenerationMethods": ["embedContent"]},
            {"name": "models/bad?key=hidden", "supportedGenerationMethods": ["generateContent"]},
        ]}, http_status=200)
        with patch("meowkeeper.integrations.vision.providers.request_json", return_value=reply) as send:
            result = health_check("gemini", settings, "fixture-key")
        self.assertEqual(result["status"], "metadata_available")
        self.assertEqual(result["models"], ["fixture"])
        self.assertEqual(send.call_args.args[0], "https://generativelanguage.googleapis.com/v1beta/models")
        self.assertEqual(send.call_args.args[1], {"x-goog-api-key": "fixture-key"})
        self.assertEqual(send.call_args.kwargs["method"], "GET")

    def test_get_transport_uses_actual_loopback_http_without_body(self):
        observed = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                observed.append((self.command,self.headers.get("Content-Length")))
                body = b'{"data":[{"id":"fixture"}]}'
                self.send_response(200)
                self.send_header("Content-Type","application/json")
                self.send_header("Content-Length",str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self,*_args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
        worker = threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        try:
            reply = request_json(f"http://127.0.0.1:{server.server_port}/models",{}, {},timeout_seconds=3,method="GET")
            self.assertEqual(reply.http_status,200)
            self.assertTrue(reply.request_dispatched)
            timing = dict(reply.http_timings)
            self.assertEqual(set(timing),{"http_started_at","response_received_at"})
            self.assertLessEqual(datetime.fromisoformat(timing["http_started_at"]),datetime.fromisoformat(timing["response_received_at"]))
            self.assertEqual(observed,[("GET",None)])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
