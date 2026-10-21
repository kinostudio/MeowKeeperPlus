"""Camera onboarding/relay contracts without cloud credentials or real motors."""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from meowkeeper.app.web.api import create_app
from meowkeeper.app.web.state import CoreStateFacade
from meowkeeper.integrations.xiaomi.models import XiaomiQualityProfile
from meowkeeper.integrations.xiaomi.relay_control import RelayControlClient, RelayControlError
from meowkeeper.integrations.xiaomi.relay_pairing import decode_pairing_code
from meowkeeper.integrations.xiaomi.relay_provider import (
    RelayProvider,
    StreamBridge,
    valid_rtsp_request,
)
from meowkeeper.integrations.xiaomi.setup_service import XiaomiSetupService


def fixture_service(root):
    profile = XiaomiQualityProfile("subtype=1", 848, 480, 20, "hevc", None, True)
    manager = Mock()
    manager.activate_stream.return_value = "rtsp://127.0.0.1:8554/camera_test"
    manager.verify_active_profile.return_value = profile
    bridge = SimpleNamespace(rtsp_port=8554, status=lambda: SimpleNamespace(to_dict=lambda: {"status": "running"}), stop=lambda: None)
    ready = threading.Event()
    def switch(*_args):
        ready.set()
        return {"status": "running"}
    service = XiaomiSetupService(root, state_path=Path(root)/"state.json", go2rtc=bridge,
        discovery=lambda: [{"internal_id": "camera_test", "device_id": "synthetic-did", "model": "test.camera.ptz",
                            "display_name": "隔離測試攝影機", "region": "sg", "stream_name": "camera_test",
                            "source_reference_present": True, "source_url": "xiaomi://test@192.0.2.1?did=synthetic-did&model=test.camera.ptz"}],
        profile_probe=Mock(return_value=[profile]), source_switch=Mock(side_effect=switch), source_manager=manager,
        active_source_frame_ready=ready.is_set)
    service.refresh()
    return service, ready


def await_job(service, job):
    end=time.monotonic()+3
    while service.connection_jobs.snapshot(job["job_id"])["status"]=="running" and time.monotonic()<end:
        time.sleep(.01)
    return service.connection_jobs.snapshot(job["job_id"])


class CameraWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.service,self.ready=fixture_service(self.root)

    def tearDown(self):
        self.service.connection_jobs.close()
        self.service.relay_provider.stop()
        self.temp.cleanup()

    def test_one_click_probes_then_switches_and_remembers_mode(self):
        job=self.service.connection_jobs.start("camera_test","local")
        result=await_job(self.service,job)
        self.assertTrue(result["success"])
        self.service.profile_probe.assert_called_once()
        self.assertEqual(self.service.profile_probe.call_args.args[1],("subtype=1",))
        self.service.source_switch.assert_called_once()
        self.assertEqual(self.service._load_state()["connection_modes"]["camera_test"],"local")
        self.assertNotIn("xiaomi://",json.dumps(result))

    def test_failed_probe_never_switches_existing_source(self):
        self.service.profile_probe.return_value=[]
        result=await_job(self.service,self.service.connection_jobs.start("camera_test","local"))
        self.assertFalse(result["success"])
        self.service.source_switch.assert_not_called()
        # A second explicit attempt must re-probe a prior failed result.
        await_job(self.service,self.service.connection_jobs.start("camera_test","local"))
        self.assertEqual(self.service.profile_probe.call_count,2)

    def test_saved_profile_reverified_without_sd_probe(self):
        self.service.camera_preferences.remember("camera_test","subtype=3","camera_test")
        result=await_job(self.service,self.service.connection_jobs.start("camera_test","local"))
        self.assertTrue(result["success"])
        self.service.profile_probe.assert_not_called()
        self.assertEqual(self.service.source_manager.verify_active_profile.call_args.args[1],"subtype=3")

    def test_double_click_returns_same_work_and_no_frame_cannot_pass(self):
        entered,release=threading.Event(),threading.Event()
        def probe(*_args):
            entered.set();release.wait(2)
            return []
        self.service.profile_probe.side_effect=probe
        first=self.service.connection_jobs.start("camera_test","local")
        self.assertTrue(entered.wait(1))
        second=self.service.connection_jobs.start("camera_test","local")
        self.assertEqual(first["job_id"],second["job_id"])
        with self.assertRaises(ValueError):
            self.service.connection_jobs.start("camera_test", "relay")
        release.set();await_job(self.service,first)
        self.service.profile_probe.side_effect=None
        self.service.profile_probe.return_value=[XiaomiQualityProfile("subtype=1",848,480,20,"hevc",None,True)]
        self.service.source_switch.side_effect=None
        self.service.source_switch.return_value={"status":"starting"}
        self.service.connection_jobs.timeout=.02
        result=await_job(self.service,self.service.connection_jobs.start("camera_test","local"))
        self.assertEqual(result["status"],"failed")
        self.assertFalse(result["success"])

    def test_api_auth_and_job_validation(self):
        facade=CoreStateFacade()
        facade._collect_application_resources=Mock(return_value={})
        app=create_app(facade=facade,token="x"*32,integrations=self.service)
        with TestClient(app) as client:
            path="/api/v1/cameras/xiaomi/devices/camera_test/connect"
            self.assertEqual(client.post(path,json={}).status_code,401)
            auth={"Authorization":"Bearer "+"x"*32,"X-MeowKeeper-CSRF":app.state.authenticator.csrf_token}
            self.assertEqual(client.post(path,json={"connection_mode":[]},headers=auth).status_code,422)
            response=client.post(path,json={"connection_mode":"local"},headers=auth)
            self.assertEqual(response.status_code,202)
            job=response.json();await_job(self.service,job)
            result=client.get("/api/v1/cameras/xiaomi/setup/operations/"+job["job_id"],headers=auth)
            self.assertTrue(result.json()["success"])
            self.assertEqual(client.get("/api/v1/cameras/xiaomi/relay-provider").status_code,401)

    def test_provider_pair_code_matches_device_no_ai_or_secret_in_state(self):
        network={"tailscale_ip":"100.64.0.10","devices":[{"device_ref":"peer","online":True,"tailnet_ips":["100.64.0.11"]}]}
        provider=RelayProvider(self.service,network=lambda:network,firewall=lambda _peer:True,
            seal=lambda path,nonce:path.write_bytes(b"sealed"))
        with patch.object(provider,"_start_servers",side_effect=lambda _ip:setattr(provider,"running",True)):
            result=provider.enable("camera_test","peer")
        claim=decode_pairing_code(result["pairing_code"])
        self.assertTrue(claim.matches_device(did="synthetic-did",model="test.camera.ptz"))
        self.assertLessEqual(claim.expires_at-time.time(),600)
        self.assertTrue(result["relay_only"])
        self.service.source_switch.assert_not_called()
        self.assertNotIn(claim.nonce,provider.path.read_text())
        self.assertNotIn("pairing_code",provider.status())
        provider.stop()
        self.assertFalse(provider.secret_path.exists())
        self.assertFalse(provider.path.exists())

    def test_provider_rejects_wrong_peer_or_missing_firewall(self):
        provider=self.service.relay_provider
        provider.network=lambda:{"tailscale_ip":"100.64.0.10","devices":[]}
        with self.assertRaises(ValueError):provider.enable("camera_test","peer")
        self.service.source_manager.activate_stream.assert_not_called()

    def test_provider_api_requires_csrf_and_pairing_is_single_read_and_revocable(self):
        facade = CoreStateFacade()
        facade._collect_application_resources = Mock(return_value={})
        facade._callbacks["tailscale_status"] = lambda: {"tailscale_ip":"100.64.0.10", "devices":[
            {"device_ref":"peer", "online":True, "tailnet_ips":["100.64.0.11"]}]}
        provider = self.service.relay_provider
        provider.seal = lambda path, _nonce: path.write_bytes(b"sealed")
        app = create_app(facade=facade, token="x"*32, integrations=self.service)
        auth = {"Authorization":"Bearer "+"x"*32, "X-MeowKeeper-CSRF":app.state.authenticator.csrf_token}
        base = "/api/v1/cameras/xiaomi/relay-provider"
        with TestClient(app) as client, patch("meowkeeper.integrations.xiaomi.relay_firewall.RelayFirewall.verified", return_value=True), \
                patch.object(provider, "_start_servers", side_effect=lambda _ip: setattr(provider, "running", True)):
            self.assertEqual(client.post(base+"/enable", json={}, headers={"Authorization":"Bearer "+"x"*32}).status_code, 403)
            self.assertEqual(client.post(base+"/enable", json={"device_id":"camera_test","peer_ref":"peer"}, headers=auth).status_code, 200)
            end = time.monotonic()+3
            while provider.starting and time.monotonic()<end:
                time.sleep(.01)
            self.assertTrue(client.get(base, headers=auth).json()["running"])
            code = client.post(base+"/pairing", json={}, headers=auth)
            self.assertEqual(code.status_code, 200)
            self.assertTrue(decode_pairing_code(code.json()["pairing_code"]).matches_device(did="synthetic-did",model="test.camera.ptz"))
            self.assertEqual(client.post(base+"/pairing", json={}, headers=auth).status_code, 422)
            self.assertTrue(client.post(base+"/revoke", json={}, headers=auth).json()["success"])
            self.assertFalse(provider.running)
            self.assertEqual(client.post(base+"/pairing", json={}, headers=auth).status_code, 422)
            self.service.source_switch.assert_not_called()

    def test_fixed_stream_tcp_only_and_peer_bound_bridge(self):
        def request(target,transport="RTP/AVP/TCP;unicast;interleaved=0-1"):
            return f"SETUP {target} RTSP/1.0\r\nCSeq: 2\r\nTransport: {transport}\r\n\r\n".encode()
        self.assertTrue(valid_rtsp_request(request("rtsp://100.64.0.10/camera_test/trackID=0"),"camera_test"))
        for target in ("rtsp://100.64.0.10/other","rtsp://100.64.0.10/camera_test/trackID=../../other"):
            self.assertFalse(valid_rtsp_request(request(target),"camera_test"))
        self.assertFalse(valid_rtsp_request(request("rtsp://100.64.0.10/camera_test","RTP/AVP;client_port=5000-5001"),"camera_test"))
        provider=self.service.relay_provider
        provider.config={"peer_ip":"100.64.0.11"}
        server=StreamBridge(provider,("127.0.0.1",0))
        try:
            self.assertFalse(server.verify_request(None,("100.64.0.12",10)))
            self.assertTrue(server.verify_request(None,("100.64.0.11",10)))
        finally:server.server_close()

    def test_remote_absolute_payload_bounds_and_position_proof(self):
        client=RelayControlClient("100.64.0.10","a"*32)
        for value in (0,102,True,None):
            with self.assertRaises(RelayControlError):client.request("ptz_position",angle=value,elevation=40)
        provider=self.service.relay_provider
        provider.config={"stream_name":"camera_test"};provider.running=True
        self.service.experimental_ptz_enabled=True
        client=Mock()
        client.xiaomi_ptz_state.return_value={"connected":True,"position":{"angle":50,"elevation":40,"ret":0},"capabilities":{"absolute_move":True}}
        client.xiaomi_ptz_action.return_value={"connected":True}
        self.service.ptz_client=client
        self.assertTrue(provider.dispatch({"action":"ptz_refresh"})["position_readback_verified"])
        self.assertTrue(provider.dispatch({"action":"ptz_position","angle":50,"elevation":43})["success"])
        self.assertEqual(client.xiaomi_ptz_action.call_args.args[1],"stop")
        client.xiaomi_ptz_action.return_value = {}
        self.assertFalse(provider.dispatch({"action":"ptz_stop"})["success"])
        self.assertFalse(provider.dispatch({"action":"ptz_position","angle":50,"elevation":43})["success"])
        client.xiaomi_ptz_state.return_value["position"]={"ret":-1}
        with self.assertRaises(ValueError):provider.dispatch({"action":"ptz_position","angle":50,"elevation":43})


if __name__=="__main__":unittest.main()
