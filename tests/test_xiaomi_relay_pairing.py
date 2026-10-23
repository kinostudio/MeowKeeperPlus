from __future__ import annotations

import json
import secrets
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from meowkeeper.integrations.xiaomi.relay_pairing import (
    RelayPairing,
    decode_pairing_code,
    device_fingerprint,
    encode_pairing_code,
    validated_relay_url,
)
from meowkeeper.integrations.xiaomi.setup_service import XiaomiSetupService


class RemotePairingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.did = "private-test-did"
        self.model = "chuangmi.camera.079ae2"
        nonce = secrets.token_hex(16)
        self.pairing = RelayPairing(
            self.model, "sg", "100.64.0.43", 28554, "c701_sd", nonce,
            device_fingerprint(nonce, self.did, self.model), int(time.time()) + 600,
        )

    def test_pairing_expires_and_rejects_non_tailnet(self) -> None:
        code = encode_pairing_code(self.pairing)
        self.assertEqual(decode_pairing_code(code).rtsp_url, "rtsp://100.64.0.43:28554/c701_sd")
        with self.assertRaises(ValueError):
            decode_pairing_code(code, now=self.pairing.expires_at + 1)
        with self.assertRaises(ValueError):
            validated_relay_url(address="192.168.1.1", port=28554, stream="c701_sd")

    def test_pair_save_and_activate_without_secret_persistence(self) -> None:
        sources = [{
            "internal_id": "c701", "device_id": self.did, "model": self.model,
            "region": "sg", "display_name": "C701", "source_url": "xiaomi://secret@192.0.2.1?did=private-test-did",
        }]
        switched: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            service = XiaomiSetupService(
                directory, state_path=Path(directory) / "state.json", discovery=lambda: sources,
                source_switch=lambda url, _device, _quality: switched.append(url) or {"status": "starting"},
                active_source_url=lambda: switched[-1] if switched else None,
            )
            service.refresh()
            with patch.object(service, "_probe_remote_jpeg", return_value=True) as probe:
                self.assertFalse(service.pair_remote("c701", encode_pairing_code(self.pairing).replace("MKP1.", "bad."))["success"])
                self.assertTrue(service.pair_remote("c701", encode_pairing_code(self.pairing))["success"])
                self.assertTrue(service.activate_remote("c701")["success"])
                self.assertEqual(probe.call_count, 2)
            self.assertEqual(switched, [self.pairing.rtsp_url])
            saved = (Path(directory) / "xiaomi-remote-pairs.json").read_text(encoding="utf-8")
            self.assertNotIn(self.did, saved)
            self.assertNotIn("secret", saved)
            self.assertNotIn(self.pairing.fingerprint, saved)
            self.assertTrue(service.devices()[0]["remote_relay_paired"])
            self.assertEqual(service.devices()[0]["connection_mode"], "remote_relay")
            self.assertFalse(service.devices()[0]["quality_switch_available"])
            rejected = service.set_live_profile("c701", "subtype=3")
            self.assertEqual(rejected["status"], "remote_control_unavailable")
            self.assertFalse(rejected["success"])
            self.assertEqual(switched, [self.pairing.rtsp_url])

    def test_wrong_device_and_offline_relay_do_not_replace_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = XiaomiSetupService(
                directory, state_path=Path(directory) / "state.json",
                discovery=lambda: [{"internal_id": "c701", "device_id": "another-did", "model": self.model, "region": "sg"}],
                source_switch=lambda *_: self.fail("source must not switch"),
            )
            service.refresh()
            with patch.object(service, "_probe_remote_jpeg", return_value=False):
                self.assertFalse(service.pair_remote("c701", encode_pairing_code(self.pairing))["success"])
            self.assertFalse((Path(directory) / "xiaomi-remote-pairs.json").exists())

    def test_remote_profile_requires_verified_frame_and_ptz_requires_real_endpoint(self) -> None:
        active = [self.pairing.rtsp_url]
        with tempfile.TemporaryDirectory() as directory:
            service = XiaomiSetupService(
                directory, state_path=Path(directory) / "state.json",
                discovery=lambda: [{"internal_id": "c701", "device_id": self.did,
                                    "model": self.model, "region": "sg"}],
                active_source_url=lambda: active[0],
            )
            service.refresh()
            service._relay_pairs["c701"] = {
                "model": self.model, "tailnet_ip": self.pairing.tailnet_ip,
                "rtsp_port": self.pairing.rtsp_port, "stream_name": self.pairing.stream_name,
            }

            class FakeControl:
                def __init__(self, response: dict[str, object]) -> None:
                    self.response = response

                def request(self, *_args: object, **_kwargs: object) -> dict[str, object]:
                    return self.response

            with patch.object(service, "_relay_control_client", return_value=FakeControl({
                "success": True, "video_verified": True, "status": "verified",
            })):
                self.assertTrue(service.set_live_profile("c701", "subtype=3")["success"])
            restored = XiaomiSetupService(directory, state_path=Path(directory) / "state.json")
            self.assertEqual(restored.camera_preferences.get("remote:c701")["profile"], "subtype=3")
            self.assertEqual(restored.camera_preferences.get("remote:c701")["stream_name"], "c701_sd")
            self.assertIsNone(restored.camera_preferences.get("c701"))
            with patch.object(service, "_relay_control_client", return_value=FakeControl({
                "success": True, "video_verified": False,
            })):
                self.assertFalse(service.set_live_profile("c701", "subtype=2")["success"])
            self.assertEqual(service.camera_preferences.get("remote:c701")["profile"], "subtype=3")
            with patch.object(service, "_relay_control_client", return_value=FakeControl({
                "success": False, "status": "ptz_unavailable", "ptz_available": False,
            })):
                self.assertFalse(service.ptz_action("c701", action="move", direction="left")["success"])
                self.assertFalse(service.ptz_status("c701")["ptz_available"])

    def test_edited_saved_endpoint_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "xiaomi-remote-pairs.json"
            path.write_text(json.dumps({"version": 1, "pairs": {"c701": {
                "model": self.model, "region": "sg", "tailnet_ip": "127.0.0.1", "rtsp_port": 28554,
                "stream_name": "c701_sd",
            }}}), encoding="utf-8")
            service = XiaomiSetupService(
                directory, state_path=Path(directory) / "state.json",
                discovery=lambda: [{"internal_id": "c701", "device_id": self.did, "model": self.model, "region": "sg"}],
                source_switch=lambda *_: self.fail("source must not switch"),
            )
            service.refresh()
            self.assertFalse(service.activate_remote("c701")["success"])

    def test_remote_resume_requires_restored_profile_and_fresh_frame(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            switched = []
            service = XiaomiSetupService(
                directory, state_path=path,
                discovery=lambda: [{"internal_id": "c701", "device_id": self.did,
                                    "model": self.model, "region": "sg"}],
                source_switch=lambda _url, _device, quality: switched.append(quality.name) or {"status": "running"},
            )
            service.refresh()
            service._relay_pairs["c701"] = {
                "model": self.model, "tailnet_ip": self.pairing.tailnet_ip,
                "rtsp_port": self.pairing.rtsp_port, "stream_name": self.pairing.stream_name,
            }
            service.camera_preferences.remember("remote:c701", "subtype=3", "c701_sd")
            from unittest.mock import Mock

            control = Mock()
            control.request.return_value = {"success": True, "video_verified": False}
            with patch.object(service, "_relay_control_client", return_value=control), \
                    patch.object(service, "_probe_remote_jpeg", return_value=True) as probe:
                self.assertFalse(service.activate_remote("c701")["success"])
                self.assertEqual(switched, [])
                probe.assert_not_called()
                control.request.return_value = {"success": True, "video_verified": True}
                self.assertTrue(service.activate_remote("c701")["success"])
                control.request.assert_called_with("profile", profile="subtype=3")
                self.assertEqual(switched, ["subtype=3"])
                self.assertEqual(service.status()["selected_profile"], "subtype=3")
                service._relay_pairs["c701"]["stream_name"] = "another_stream"
                self.assertFalse(service.activate_remote("c701")["success"])
                self.assertEqual(switched, ["subtype=3"])


if __name__ == "__main__":
    unittest.main()
