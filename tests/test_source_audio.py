"""Audio opt-in reuses the bounded profile switch; no real camera is touched."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from meowkeeper.app.web.api import create_app
from meowkeeper.integrations.xiaomi.camera_preferences import CameraPreferences
from meowkeeper.integrations.xiaomi.device_discovery import Go2RtcXiaomiDiscovery
from tests.test_camera_connection_workflow import await_job, fixture_service


class SourceAudioTests(unittest.TestCase):
    def test_source_audio_proof_requires_received_packets_not_advertised_media(self):
        client = Mock()
        bridge = Go2RtcXiaomiDiscovery(client)
        client.stream_status.return_value = {"producers": [{"medias": ["audio, recvonly, OPUS/48000/2"]}]}
        self.assertIsNone(bridge._received_audio_codec("camera_test"))
        client.stream_status.return_value = {"producers": [{"receivers": [
            {"codec": {"codec_name": "opus"}, "packets": 9}]}]}
        self.assertEqual(bridge._received_audio_codec("camera_test"), "opus")
        client.stream_status.side_effect = RuntimeError("synthetic timeout")
        self.assertIsNone(bridge._received_audio_codec("camera_test"))

    def test_explicit_audio_zero_is_preserved_until_user_opts_in(self):
        source = "xiaomi://test@192.0.2.1?audio=0&subtype=1&model=test.camera"
        self.assertEqual(parse_qs(urlsplit(Go2RtcXiaomiDiscovery._with_profile(source, "subtype=3")).query)["audio"], ["0"])
        enabled = Go2RtcXiaomiDiscovery._with_profile(source, "subtype=3", audio_enabled=True)
        query = parse_qs(urlsplit(enabled).query)
        self.assertEqual(query["audio"], ["1"])
        self.assertEqual(query["subtype"], ["3"])
        self.assertEqual(query["model"], ["test.camera"])
        with self.assertRaises(ValueError):
            Go2RtcXiaomiDiscovery._with_profile(source, "subtype=3", audio_enabled="1")

    def test_safe_audio_preference_survives_quality_change_and_reload(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "preferences.json"
            prefs = CameraPreferences(path)
            prefs.remember("camera-test", "subtype=1", "camera_test", audio_enabled=True)
            prefs.remember("camera-test", "subtype=3", "camera_test")
            self.assertTrue(CameraPreferences(path).get("camera-test")["audio_enabled"])
            self.assertNotIn("xiaomi://", path.read_text())

    def test_api_requires_auth_csrf_boolean_and_explicit_reconnect_confirmation(self):
        switch = Mock(return_value={"success": True, "audio_verified": False})
        app = create_app(token="t" * 32, integrations=SimpleNamespace(set_live_profile=switch))
        payload = {"profile":"subtype=1", "audio_enabled":True, "confirm_reconnect":True}
        with TestClient(app) as client:
            route = "/api/v1/cameras/xiaomi/devices/test/profile"
            self.assertEqual(client.post(route, json=payload).status_code, 401)
            headers = {"Authorization":"Bearer " + "t" * 32}
            self.assertEqual(client.post(route, json=payload, headers=headers).status_code, 403)
            headers["X-MeowKeeper-CSRF"] = app.state.authenticator.csrf_token
            self.assertEqual(client.post(route, json={**payload, "confirm_reconnect":False}, headers=headers).status_code, 422)
            self.assertEqual(client.post(route, json={**payload, "audio_enabled":"1"}, headers=headers).status_code, 422)
            switch.assert_not_called()
            self.assertEqual(client.post(route, json=payload, headers=headers).status_code, 200)
            switch.assert_called_once_with("test", "subtype=1", audio_enabled=True)

    def test_one_stream_opt_in_persists_but_no_track_is_not_audio_success(self):
        with tempfile.TemporaryDirectory() as temp:
            service, _ready = fixture_service(Path(temp))
            try:
                self.assertTrue(await_job(service, service.connection_jobs.start("camera_test", "local"))["success"])
                service.source_manager.activate_stream.reset_mock()
                result = service.set_live_profile("camera_test", "subtype=1", audio_enabled=True)
                self.assertTrue(result["video_verified"])
                self.assertFalse(result["audio_verified"])
                self.assertIn("不能宣稱收音成功", result["message_zh_tw"])
                call = service.source_manager.activate_stream.call_args
                self.assertEqual(call.args[:2], ("camera_test", "camera_test"))
                self.assertEqual(call.kwargs, {"audio_enabled":True})
                self.assertTrue(service.camera_preferences.get("camera_test")["audio_enabled"])
                service.source_manager.activate_stream.reset_mock()
                _ready.clear()  # Simulate restart: saved preference is not current-frame proof.
                service.activate("camera_test")
                self.assertEqual(service.source_manager.activate_stream.call_args.kwargs, {"audio_enabled":True})
            finally:
                service.connection_jobs.close()
                service.relay_provider.stop()

    def test_failed_audio_reconnect_restores_previous_audio_preference(self):
        with tempfile.TemporaryDirectory() as temp:
            service, _ready = fixture_service(Path(temp))
            try:
                self.assertTrue(await_job(service, service.connection_jobs.start("camera_test", "local"))["success"])
                service.camera_preferences.remember("camera_test", "subtype=1", "camera_test", audio_enabled=False)
                profile = service.source_manager.verify_active_profile.return_value
                service.source_manager.verify_active_profile.side_effect = [RuntimeError("synthetic failure"), profile]
                service.source_manager.activate_stream.reset_mock()
                result = service.set_live_profile("camera_test", "subtype=1", audio_enabled=True)
                self.assertFalse(result["success"])
                self.assertTrue(result["rollback_verified"])
                self.assertEqual([call.kwargs for call in service.source_manager.activate_stream.call_args_list],
                                 [{"audio_enabled":True}, {"audio_enabled":False}])
                self.assertFalse(service.camera_preferences.get("camera_test")["audio_enabled"])
            finally:
                service.connection_jobs.close()
                service.relay_provider.stop()
