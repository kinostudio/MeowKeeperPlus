from __future__ import annotations

import json
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from meowkeeper.integrations.go2rtc.client import Go2RtcClient
from meowkeeper.integrations.go2rtc.models import Go2RtcStatus
from meowkeeper.integrations.go2rtc.process import Go2RtcProcess
from meowkeeper.integrations.go2rtc.redaction import mask_response_body
from meowkeeper.integrations.xiaomi.login_diagnostics import (
    classify_login_flow_response,
    classify_login_response,
)
from meowkeeper.integrations.xiaomi.setup_service import XiaomiSetupService


class _FakeGo2Rtc:
    api_url = "http://127.0.0.1:1984"
    rtsp_port = 8554

    def status(self):
        return Go2RtcStatus("ready", True, False, version="v1.9.14")


class XiaomiLoginDiagnosticTests(unittest.TestCase):
    def test_post_200_empty_body_is_login_request_success(self) -> None:
        diagnostic = classify_login_flow_response(b"", phase="post", http_status=200)
        self.assertEqual(diagnostic.classification, "login_success")
        self.assertEqual(diagnostic.phase, "post")
        self.assertIn("GET", diagnostic.message_zh_tw)

    def test_get_200_account_list_requires_and_matches_new_uid(self) -> None:
        diagnostic = classify_login_flow_response(
            ["existing", "new-user"],
            phase="get",
            expected_uid="new-user",
            http_status=200,
        )
        self.assertEqual(diagnostic.classification, "login_success")
        self.assertTrue(diagnostic.account_uid_present)
        self.assertNotIn("new-user", json.dumps(diagnostic.to_dict(), ensure_ascii=False))

    def test_get_200_account_list_without_new_uid_is_login_failed(self) -> None:
        diagnostic = classify_login_flow_response(
            ["existing"], phase="get", expected_uid="new-user", http_status=200
        )
        self.assertEqual(diagnostic.classification, "login_failed")
        self.assertFalse(diagnostic.account_uid_present)

    def test_legacy_200_account_array_is_not_unknown_error(self) -> None:
        diagnostic = classify_login_response(["account"], http_status=200)
        self.assertEqual(diagnostic.classification, "login_success")

    def test_flow_500_is_login_failed(self) -> None:
        diagnostic = classify_login_flow_response(
            "unexpected upstream error", phase="post", http_status=500
        )
        self.assertEqual(diagnostic.classification, "login_failed")

    def test_post_200_unexpected_body_is_login_failed(self) -> None:
        diagnostic = classify_login_flow_response(
            {"message": "unexpected response"}, phase="post", http_status=200
        )
        self.assertEqual(diagnostic.classification, "login_failed")

    def test_flow_401_challenges_keep_specific_categories(self) -> None:
        self.assertEqual(
            classify_login_flow_response(
                {"captcha_present": True},
                phase="post",
                http_status=401,
            ).classification,
            "captcha_required",
        )
        self.assertEqual(
            classify_login_flow_response(
                {"verify_email": True}, phase="post", http_status=401
            ).classification,
            "verification_required",
        )
    def test_go2rtc_persistent_log_pump_masks_before_writing(self) -> None:
        source = StringIO("password=private rtsp://admin:secret@192.0.2.1/cam\n")
        target = StringIO()
        Go2RtcProcess._pump_redacted_log(source, target)
        self.assertNotIn("private", target.getvalue())
        self.assertNotIn("192.0.2.1/cam", target.getvalue())

    def test_localhost_client_captures_status_type_and_masked_body(self) -> None:
        class _Headers:
            def get(self, name: str, default: str | None = None) -> str | None:
                return "application/json" if name == "Content-Type" else default

        class _Response:
            status = 401
            headers = _Headers()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size: int = -1) -> bytes:
                return b'{"code":70016,"password":"private"}'

            def getcode(self) -> int:
                return self.status

        with patch("urllib.request.urlopen", return_value=_Response()):
            result = Go2RtcClient().get_diagnostic("/api/xiaomi")
        self.assertEqual(result.http_status, 401)
        self.assertEqual(result.content_type, "application/json")
        self.assertNotIn("private", result.body_masked)

    def test_post_xiaomi_login_uses_form_body_not_url(self) -> None:
        class _Headers:
            def get(self, name: str, default: str | None = None) -> str | None:
                return "application/json" if name == "Content-Type" else default

        class _Response:
            status = 200
            headers = _Headers()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size: int = -1) -> bytes:
                return b""

            def getcode(self) -> int:
                return self.status

        captured = []

        def _open(request, timeout):
            captured.append((request, timeout))
            return _Response()

        with patch("urllib.request.urlopen", side_effect=_open):
            result = Go2RtcClient().post_xiaomi_login(username="user", password="private")
        self.assertEqual(result.http_status, 200)
        request, _timeout = captured[0]
        self.assertNotIn("private", request.full_url)
        self.assertIn(b"password=private", request.data)
        self.assertEqual(request.get_method(), "POST")

    def test_70016_without_captcha_is_upstream_not_password_error(self) -> None:
        diagnostic = classify_login_response(
            {
                "code": 70016,
                "description": "登入驗證失敗",
                "captchaUrl": None,
                "securityStatus": 0,
                "result": "error",
            },
            http_status=401,
            content_type="application/json",
        )
        self.assertEqual(diagnostic.classification, "upstream_api_changed")
        self.assertNotEqual(diagnostic.classification, "invalid_credentials")
        self.assertIn("帳密可能正確", diagnostic.message_zh_tw)
        self.assertTrue(diagnostic.is_70016)

    def test_explicit_challenge_categories_are_distinct(self) -> None:
        self.assertEqual(
            classify_login_response({"code": 1, "description": "invalid password"}).classification,
            "invalid_credentials",
        )
        self.assertEqual(
            classify_login_response({"need_verify": True, "notificationUrl": "https://private.invalid/x"}).classification,
            "verification_required",
        )
        self.assertEqual(
            classify_login_response({"captchaUrl": "https://private.invalid/captcha"}).classification,
            "captcha_required",
        )
        self.assertEqual(
            classify_login_response({"securityStatus": 1, "description": "risk control"}).classification,
            "xiaomi_risk_control",
        )
        self.assertEqual(
            classify_login_response({}, http_status=401).classification,
            "unsupported_login_flow",
        )

    def test_network_and_malformed_responses_do_not_crash(self) -> None:
        self.assertEqual(
            classify_login_response(network_error="timeout").classification,
            "network_error",
        )
        self.assertEqual(
            classify_login_response("<html>gateway error</html>", http_status=502).classification,
            "upstream_api_changed",
        )

    def test_body_and_stderr_are_masked_and_bounded(self) -> None:
        body = {
            "code": 70016,
            "password": "do-not-store",
            "passToken": "token-do-not-store",
            "location": "https://account.invalid/login?serviceToken=secret",
            "captchaUrl": "https://account.invalid/captcha?code=secret",
            "email": "private@example.invalid",
        }
        diagnostic = classify_login_response(body, stderr="password=do-not-store rtsp://admin:secret@192.0.2.1/cam")
        rendered = json.dumps(diagnostic.to_dict(), ensure_ascii=False)
        self.assertNotIn("do-not-store", rendered)
        self.assertNotIn("token-do-not-store", rendered)
        self.assertNotIn("192.0.2.1/cam", rendered)
        self.assertNotIn("private@example.invalid", rendered)
        self.assertLessEqual(len(diagnostic.response_body_masked), 4096)
        self.assertLessEqual(len(mask_response_body(body)), 4096)

    def test_login_error_structure_exposes_presence_only(self) -> None:
        diagnostic = classify_login_response(
            {"code": 1, "description": "需要驗證"},
            login_error={"VerifyPhone": "+886900000000", "Captcha": "secret-image"},
        )
        self.assertTrue(diagnostic.login_error["verify_phone_present"])
        self.assertTrue(diagnostic.login_error["captcha_present"])
        self.assertNotIn("+886900000000", json.dumps(diagnostic.to_dict(), ensure_ascii=False))
        self.assertNotIn("secret-image", json.dumps(diagnostic.to_dict(), ensure_ascii=False))

    def test_service_persists_cooldown_and_requires_explicit_retry(self) -> None:
        with tempfile.TemporaryDirectory(prefix="meowkeeper-xiaomi-login-") as directory:
            state_path = Path(directory) / "state.json"
            service = XiaomiSetupService(
                directory,
                go2rtc=_FakeGo2Rtc(),
                state_path=state_path,
                login_cooldown_seconds=60,
            )
            result = service.record_login_diagnostic(
                body={"code": 70016, "description": "登入驗證失敗", "captchaUrl": None, "securityStatus": 0, "result": "error"},
                http_status=401,
                content_type="application/json",
                stderr="password=secret",
                now=100,
            )
            self.assertEqual(result["setup_stage"], "login_blocked")
            self.assertEqual(result["login_classification"], "upstream_api_changed")
            self.assertTrue(result["login_retry"]["cooldown_active"])
            self.assertFalse(service.can_attempt_login(now=120))
            self.assertTrue(service.can_attempt_login(explicit_retry=True, now=120))
            retry = service.request_explicit_login_retry(now=120)
            self.assertTrue(retry["retry_allowed"])
            self.assertEqual(retry["setup_stage"], "waiting_for_login")
            self.assertFalse(retry["login_retry"]["cooldown_active"])
            saved = state_path.read_text(encoding="utf-8")
            self.assertNotIn("password=secret", saved)
            self.assertIn("upstream_api_changed", saved)

    def test_web_api_exposes_capture_and_explicit_retry_only(self) -> None:
        try:
            from fastapi.testclient import TestClient

            from meowkeeper.app.web.api import create_app
        except ImportError:
            self.skipTest("FastAPI runtime 尚未安裝")
        with tempfile.TemporaryDirectory(prefix="meowkeeper-xiaomi-api-") as directory:
            service = XiaomiSetupService(
                directory,
                go2rtc=_FakeGo2Rtc(),
                state_path=Path(directory) / "state.json",
                login_cooldown_seconds=60,
            )
            app = create_app(token="a" * 32, integrations=service)
            client = TestClient(app)
            headers = {
                "Authorization": "Bearer " + "a" * 32,
                "X-MeowKeeper-CSRF": app.state.authenticator.csrf_token,
            }
            response = client.post(
                "/api/v1/cameras/xiaomi/setup/login-diagnostic",
                headers=headers,
                json={
                    "http_status": 401,
                    "content_type": "application/json",
                    "body": {"code": 70016, "captchaUrl": None, "securityStatus": 0, "result": "error"},
                    "stderr": "token=private",
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["login_classification"], "upstream_api_changed")
            self.assertNotIn("private", response.text)
            retry = client.post("/api/v1/cameras/xiaomi/setup/retry-login", headers=headers, json={})
            self.assertEqual(retry.status_code, 200, retry.text)
            self.assertTrue(retry.json()["retry_allowed"])


if __name__ == "__main__":
    unittest.main()
