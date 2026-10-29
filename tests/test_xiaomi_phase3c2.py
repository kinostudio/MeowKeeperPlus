from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from meowkeeper.integrations.go2rtc.client import Go2RtcClient
from meowkeeper.integrations.xiaomi.device_discovery import Go2RtcXiaomiDiscovery
from meowkeeper.integrations.xiaomi.models import XiaomiQualityProfile
from meowkeeper.integrations.xiaomi.setup_service import XiaomiSetupService


class FakeXiaomiClient:
    def __init__(self) -> None:
        self.put_names: list[str] = []
        self.deleted: list[str] = []

    def xiaomi_accounts(self) -> list[str]:
        return ["account-reference"]

    def xiaomi_sources(self, _account: str, *, region: str) -> list[dict[str, Any]]:
        return [
            {
                "name": "小米智能攝影機 標準版2K",
                "info": "192.0.2.10 / 02:00:00:00:00:01",
                "url": "xiaomi://session@192.0.2.20?did=private-did&model=isa.camera.hlc7",
                "region": region,
            }
        ]

    def put_stream(self, name: str, _source: str) -> str:
        self.put_names.append(name)
        return ""

    def delete_stream(self, name: str) -> str:
        self.deleted.append(name)
        return ""

    def probe_stream(self, _name: str) -> dict[str, Any]:
        return {}

    def stream_status(self, _name: str) -> dict[str, Any]:
        return {"producers": [], "consumers": None}


class P2PidMissingClient(FakeXiaomiClient):
    def probe_stream(self, _name: str) -> dict[str, Any]:
        raise RuntimeError("go2rtc localhost API 回應 HTTP 500：streams: xiaomi: no p2pid allocated, please reboot device")


class RegionClient(FakeXiaomiClient):
    def __init__(self, *, working_region: str | None = "sg") -> None:
        super().__init__()
        self.working_region = working_region
        self.sources_by_name: dict[str, str] = {}
        self.active: set[str] = set()
        self.peak_producers = 0

    def xiaomi_sources(self, _account: str, *, region: str) -> list[dict[str, Any]]:
        if region not in {"cn", "sg"}:
            return []
        return [{
            "name": "測試攝影機",
            "info": "192.0.2.10",
            "url": f"xiaomi://account:{region}@192.0.2.10?did=private-did&model=chuangmi.camera.026c02",
        }]

    def put_stream(self, name: str, source: str) -> str:
        self.put_names.append(name)
        self.sources_by_name[name] = source
        self.active.add(name)
        self.peak_producers = max(self.peak_producers, len(self.active))
        return ""

    def has_jpeg_frame(self, name: str, *, timeout_seconds: float) -> bool:
        self.assert_timeout = timeout_seconds
        return self.working_region is not None and f":{self.working_region}@" in self.sources_by_name[name]

    def delete_stream(self, name: str) -> str:
        self.deleted.append(name)
        self.active.discard(name)
        return ""


class XiaomiPhase3C2Tests(unittest.TestCase):
    def test_live_quality_pairing_recovers_only_matching_local_producer(self) -> None:
        class TwoCameraClient(FakeXiaomiClient):
            def xiaomi_sources(self, _account: str, *, region: str) -> list[dict[str, Any]]:
                return [
                    {"name": "本地 2K", "info": "192.0.2.10", "region": region,
                     "url": "xiaomi://session@192.0.2.10?did=one&model=isa.camera.hlc7"},
                    {"name": "本地 360", "info": "192.0.2.11", "region": region,
                     "url": "xiaomi://session@192.0.2.11?did=two&model=chuangmi.camera.026c02"},
                ]

            def stream_status(self, _name: str) -> dict[str, Any]:
                return {"producers": [{"format_name": "xiaomi/miss", "user_agent": "CS2 (chuangmi.camera.026c02)"}]}

        client = TwoCameraClient()
        bridge = Go2RtcXiaomiDiscovery(client)
        source_url = ["rtsp://127.0.0.1:8554/xiaomi_camera_2"]
        with tempfile.TemporaryDirectory(prefix="meowkeeper-xiaomi-resume-") as directory:
            service = XiaomiSetupService(
                directory,
                state_path=Path(directory) / "state.json",
                discovery=bridge.discover,
                source_manager=bridge,
                active_source_url=lambda: source_url[0],
                source_switch=lambda *_: {"status": "running"},
            )
            service.refresh()
            selected = [item for item in service.devices() if item["selected"]]
            self.assertEqual([item["model"] for item in selected], ["chuangmi.camera.026c02"])
            with patch.object(bridge, "verify_active_profile", return_value=XiaomiQualityProfile(
                "subtype=3", 1920, 1080, 20, "hevc", None, True,
            )):
                switched = service.set_live_profile(selected[0]["internal_id"], "subtype=3")
            self.assertTrue(switched["success"])
            self.assertEqual(client.put_names, ["xiaomi_camera_2"])

            # A remote RTSP relay is video-only from this Host's perspective;
            # local profile/PTZ controls must not target the old camera.
            source_url[0] = "rtsp://100.64.0.43:28554/c701_sd"
            self.assertFalse(any(item["selected"] for item in service.devices()))
            blocked = service.set_live_profile(selected[0]["internal_id"], "subtype=2")
            self.assertFalse(blocked["success"])
            self.assertEqual(client.put_names, ["xiaomi_camera_2"])

    def test_video_profile_preserves_audio_by_default_and_explicit_setting(self) -> None:
        source = "xiaomi://session:sg@192.0.2.20?did=private-did&model=isa.camera.hlc7"
        video_only = Go2RtcXiaomiDiscovery._with_profile(source, "subtype=sd")
        query = parse_qs(urlsplit(video_only).query)
        self.assertEqual(query["audio"], ["1"])
        self.assertEqual(query["subtype"], ["sd"])

        explicit_audio = Go2RtcXiaomiDiscovery._with_profile(source + "&audio=1", "subtype=hd")
        self.assertEqual(parse_qs(urlsplit(explicit_audio).query)["audio"], ["1"])

    def test_quality_probe_uses_supported_rtsp_timeout_option(self) -> None:
        bridge = Go2RtcXiaomiDiscovery(FakeXiaomiClient())
        device = {
            "source_url": "xiaomi://session:sg@192.0.2.20?did=private-did&model=isa.camera.hlc7",
            "internal_id": "synthetic",
            "online_status": "verified_frame",
        }
        video = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=json.dumps({"streams": [{"codec_name": "hevc", "width": 640, "height": 360, "avg_frame_rate": "20/1"}]}),
            stderr="",
        )
        audio = subprocess.CompletedProcess(args=[], returncode=0, stdout='{"streams": []}', stderr="")
        with patch("meowkeeper.integrations.xiaomi.device_discovery.bundled_binary", return_value="ffprobe.exe"), patch(
            "meowkeeper.integrations.xiaomi.device_discovery.subprocess.run", side_effect=[video, audio],
        ) as run:
            result = bridge.profile_probe(device, ("subtype=sd",))
        self.assertTrue(result[0].stable)
        self.assertEqual(result[0].width, 640)
        command = run.call_args_list[0].args[0]
        self.assertIn("-timeout", command)
        self.assertNotIn("-rw_timeout", command)

    def test_auto_region_requires_frame_and_remembers_per_device_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="meowkeeper-region-") as directory:
            cache = Path(directory) / "xiaomi-regions.json"
            client = RegionClient()
            bridge = Go2RtcXiaomiDiscovery(client, region="auto", region_cache_path=cache)
            devices = bridge.discover()
            self.assertEqual(len(devices), 1)
            self.assertEqual(devices[0]["region"], "sg")
            self.assertEqual(devices[0]["online_status"], "verified_frame")
            self.assertEqual(client.peak_producers, 1)
            self.assertEqual(client.put_names, client.deleted)
            self.assertEqual(len(client.put_names), 2)  # cn failed, then sg passed.
            self.assertIn(":sg@", bridge.source_for(devices[0]["internal_id"]) or "")
            saved = cache.read_text(encoding="utf-8")
            self.assertNotIn("private-did", saved)
            self.assertNotIn("account", saved)
            self.assertNotIn("xiaomi://", saved)

            cached_client = RegionClient()
            cached_bridge = Go2RtcXiaomiDiscovery(cached_client, region="auto", region_cache_path=cache)
            cached_devices = cached_bridge.discover()
            self.assertEqual(cached_devices[0]["region"], "sg")
            self.assertEqual(cached_devices[0]["online_status"], "previously_verified")
            self.assertEqual(cached_client.put_names, [])

    def test_auto_region_does_not_guess_when_no_frame_exists(self) -> None:
        client = RegionClient(working_region=None)
        bridge = Go2RtcXiaomiDiscovery(client, region="auto")
        devices = bridge.discover()
        self.assertEqual(devices[0]["region"], "unconfirmed")
        self.assertEqual(devices[0]["online_status"], "unverified")
        self.assertFalse(devices[0]["source_reference_present"])
        self.assertIsNone(bridge.source_for(devices[0]["internal_id"]))
        self.assertEqual(client.put_names, client.deleted)

    def test_sg_fallback_is_testable_candidate_not_verified_cache(self) -> None:
        with tempfile.TemporaryDirectory(prefix="meowkeeper-region-candidate-") as directory:
            cache = Path(directory) / "regions.json"
            client = RegionClient(working_region=None)
            bridge = Go2RtcXiaomiDiscovery(
                client, region="auto", fallback_region="sg", region_cache_path=cache,
            )
            devices = bridge.discover()
            self.assertEqual(len(devices), 1)
            self.assertEqual(devices[0]["region"], "sg")
            self.assertEqual(devices[0]["online_status"], "region_candidate")
            self.assertTrue(devices[0]["source_reference_present"])
            self.assertIn(":sg@", bridge.source_for(devices[0]["internal_id"]) or "")
            self.assertFalse(cache.exists())
            self.assertEqual(client.put_names, client.deleted)

    def test_bundled_ffmpeg_accepts_real_jpeg_markers_without_saving_frame(self) -> None:
        bridge = Go2RtcXiaomiDiscovery(RegionClient(), rtsp_port=28554)
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"\xff\xd8frame\xff\xd9", stderr=b"")
        with patch("meowkeeper.integrations.xiaomi.device_discovery.bundled_binary", return_value="ffmpeg.exe"), patch(
            "meowkeeper.integrations.xiaomi.device_discovery.subprocess.run", return_value=completed,
        ) as run:
            self.assertTrue(bridge._probe_bundled_jpeg_frame("test_stream"))
        command = run.call_args.args[0]
        self.assertIn("rtsp://127.0.0.1:28554/test_stream", command)
        self.assertNotIn("-rw_timeout", command)
        self.assertTrue(run.call_args.kwargs["shell"] is False)

    def test_real_client_uses_bundled_fallback_after_jpeg_api_failure(self) -> None:
        source = "xiaomi://account:sg@192.0.2.10?did=synthetic&model=chuangmi.camera.026c02"
        bridge = Go2RtcXiaomiDiscovery(Go2RtcClient())
        with patch.object(Go2RtcClient, "put_stream"), patch.object(
            Go2RtcClient, "has_jpeg_frame", return_value=False,
        ), patch.object(Go2RtcClient, "delete_stream") as deleted, patch.object(
            Go2RtcXiaomiDiscovery, "_probe_bundled_jpeg_frame", return_value=True,
        ) as fallback:
            self.assertTrue(bridge._probe_region_frame(source, "synthetic-key", "sg"))
        fallback.assert_called_once()
        deleted.assert_called_once()

    def test_auto_region_cache_falls_back_when_primary_is_not_writable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="meowkeeper-region-fallback-") as directory:
            primary = Path(directory) / "not-a-directory"
            primary.write_text("occupied", encoding="utf-8")
            fallback = Path(directory) / "state" / "regions.json"
            client = RegionClient()
            bridge = Go2RtcXiaomiDiscovery(
                client, region="auto", region_cache_path=primary / "regions.json",
                region_cache_fallback_path=fallback,
            )
            self.assertEqual(bridge.discover()[0]["region"], "sg")
            self.assertTrue(fallback.is_file())
            self.assertNotIn("private-did", fallback.read_text(encoding="utf-8"))
            reused = Go2RtcXiaomiDiscovery(
                RegionClient(), region="auto", region_cache_path=primary / "regions.json",
                region_cache_fallback_path=fallback,
            )
            self.assertEqual(reused.discover()[0]["online_status"], "previously_verified")

    def test_discovery_uses_injected_local_rtsp_port(self) -> None:
        client = FakeXiaomiClient()
        bridge = Go2RtcXiaomiDiscovery(client, rtsp_port=28554)
        device = bridge.discover()[0]

        with tempfile.TemporaryDirectory(prefix="meowkeeper-phase3c2-port-") as directory:
            service = XiaomiSetupService(
                directory,
                state_path=Path(directory) / "state.json",
                discovery=bridge.discover,
                profile_probe=lambda _device, _names: [
                    XiaomiQualityProfile("subtype=sd", 1280, 720, 15, "h264", None, True)
                ],
                source_manager=bridge,
                source_switch=lambda url, _device, _profile: {"status": "connected", "url": url},
            )
            service.refresh()
            service.probe_device(device["internal_id"])
            result = service.activate(device["internal_id"])

        self.assertEqual(result["setup_stage"], "connected")
        self.assertEqual(bridge.rtsp_port, 28554)

    def test_real_contract_mapping_is_safe_and_opaque(self) -> None:
        client = FakeXiaomiClient()
        bridge = Go2RtcXiaomiDiscovery(client)
        items = bridge.discover()
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["model"], "isa.camera.hlc7")
        self.assertTrue(item["internal_id"].startswith("xiaomi_camera_"))
        self.assertNotIn("private-did", item["internal_id"])
        self.assertEqual(item["lan_ip"], "192.0.2.xxx")
        self.assertTrue(item["source_reference_present"])
        self.assertIn("xiaomi://", bridge.source_for(item["internal_id"]) or "")

    def test_setup_refresh_uses_opaque_id_and_activation_keeps_single_local_source(self) -> None:
        client = FakeXiaomiClient()
        bridge = Go2RtcXiaomiDiscovery(client)
        switched: list[str] = []

        def probe(_device: dict[str, Any], _names: tuple[str, ...]) -> list[XiaomiQualityProfile]:
            return [XiaomiQualityProfile("subtype=auto", 1920, 1080, 15, "h264", None, True)]

        def switch(url: str, _device: dict[str, Any], _profile: XiaomiQualityProfile) -> dict[str, str]:
            switched.append(url)
            return {"status": "connected"}

        with tempfile.TemporaryDirectory(prefix="meowkeeper-phase3c2-") as directory:
            service = XiaomiSetupService(
                directory,
                state_path=Path(directory) / "state.json",
                discovery=bridge.discover,
                profile_probe=probe,
                source_manager=bridge,
                source_switch=switch,
            )
            refreshed = service.refresh()
            item = service.devices()[0]
            service.probe_device(item["internal_id"])
            activated = service.activate(item["internal_id"])
            saved = json.loads((Path(directory) / "state.json").read_text(encoding="utf-8"))


        self.assertEqual(refreshed["setup_stage"], "account_ready")
        self.assertEqual(activated["setup_stage"], "connected")
        self.assertEqual(switched, ["rtsp://127.0.0.1:8554/xiaomi_camera_1"])
        self.assertEqual(client.put_names, ["xiaomi_camera_1"])
        self.assertNotIn("source_url", json.dumps(saved, ensure_ascii=False))
        self.assertEqual(saved["selected_stream_name"], "xiaomi_camera_1")

    def test_live_quality_switch_is_bounded_and_reuses_same_stream(self) -> None:
        client = FakeXiaomiClient()
        bridge = Go2RtcXiaomiDiscovery(client)
        with tempfile.TemporaryDirectory(prefix="meowkeeper-xiaomi-live-profile-") as directory:
            service = XiaomiSetupService(
                directory,
                state_path=Path(directory) / "state.json",
                discovery=bridge.discover,
                profile_probe=lambda _device, _names: [
                    XiaomiQualityProfile("subtype=1", 640, 360, 20, "hevc", None, True)
                ],
                source_manager=bridge,
                source_switch=lambda _url, _device, _profile: {"status": "connected"},
            )
            service.refresh()
            device_id = service.devices()[0]["internal_id"]
            service.probe_device(device_id)
            service.activate(device_id)
            with patch.object(bridge, "verify_active_profile", return_value=XiaomiQualityProfile(
                "subtype=3", 1920, 1080, 20, "hevc", None, True,
            )):
                switched = service.set_live_profile(device_id, "subtype=3")
            rejected = service.set_live_profile(device_id, "subtype=9")
            with patch.object(bridge, "verify_active_profile", side_effect=[
                RuntimeError("新畫質沒有影格"),
                XiaomiQualityProfile("subtype=3", 1920, 1080, 20, "hevc", None, True),
            ]):
                failed = service.set_live_profile(device_id, "subtype=2")
            self.assertFalse(failed["success"])
            self.assertTrue(failed["rollback_verified"])
            device = next(item for item in service.devices() if item["selected"])
            saved = json.loads((Path(directory) / "state.json").read_text(encoding="utf-8"))
            restarted = XiaomiSetupService(
                directory, state_path=Path(directory) / "state.json",
                discovery=bridge.discover, source_manager=bridge,
                source_switch=lambda _url, _device, _profile: {"status": "connected"},
            )
            restarted.refresh()
            self.assertTrue(restarted.devices()[0]["resume_available"])
            with patch.object(bridge, "verify_active_profile", return_value=XiaomiQualityProfile(
                "subtype=3", 1920, 1080, 20, "hevc", None, True,
            )) as verification:
                restored = restarted.activate(device_id)
            self.assertEqual(restored["setup_stage"], "connected")
            verification.assert_called_once_with("xiaomi_camera_1", "subtype=3")

        self.assertTrue(switched["success"])
        self.assertEqual(switched["profile"], "subtype=3")
        self.assertTrue(switched["video_verified"])
        self.assertEqual(switched["source_metadata"]["width"], 1920)
        self.assertEqual(rejected["status"], "invalid")
        self.assertEqual(client.put_names, ["xiaomi_camera_1"] * 5)
        self.assertEqual(device["selected_profile"], "subtype=3")
        self.assertEqual([item["value"] for item in device["quality_profiles"]], [
            "subtype=1", "subtype=2", "subtype=3", "subtype=auto",
            "subtype=sd", "subtype=hd", "subtype=0", "subtype=4", "subtype=5",
        ])
        self.assertEqual(saved["selected_profile"], "subtype=3")
        self.assertNotIn("source_url", json.dumps(saved, ensure_ascii=False))

    def test_async_source_start_is_pending_not_falsely_failed_or_verified(self) -> None:
        client = FakeXiaomiClient()
        bridge = Go2RtcXiaomiDiscovery(client)
        with tempfile.TemporaryDirectory(prefix="meowkeeper-xiaomi-starting-") as directory:
            service = XiaomiSetupService(
                directory,
                state_path=Path(directory) / "state.json",
                discovery=bridge.discover,
                profile_probe=lambda _device, _names: [
                    XiaomiQualityProfile("subtype=sd", 640, 360, 20, "hevc", None, True)
                ],
                source_manager=bridge,
                source_switch=lambda _url, _device, _profile: {"status": "starting"},
            )
            service.refresh()
            device = service.devices()[0]
            service.probe_device(device["internal_id"])
            result = service.activate(device["internal_id"])
        self.assertEqual(result["action_status"], "starting")
        self.assertEqual(result["setup_stage"], "activating_source")
        self.assertNotEqual(result["latest_error_code"], "SOURCE_SWITCH_FAILED")

    def test_p2pid_failure_stops_profile_variants_and_cleans_temporary_stream(self) -> None:
        client = P2PidMissingClient()
        bridge = Go2RtcXiaomiDiscovery(client)
        device = bridge.discover()[0]

        profiles = bridge.profile_probe(device, ("subtype=auto", "subtype=sd", "subtype=hd"))

        self.assertEqual(len(client.put_names), 1)
        self.assertTrue(client.put_names[0].startswith("xiaomi_probe_"))
        self.assertEqual(client.deleted, client.put_names)
        self.assertEqual(len(profiles), 3)
        self.assertIn("p2pid", profiles[0].error or "")
        self.assertEqual(profiles[1].error, "略過：來源尚未分配 p2pid。")
        self.assertEqual(profiles[2].error, "略過：來源尚未分配 p2pid。")

    def test_setup_probe_does_not_duplicate_profile_variants(self) -> None:
        client = P2PidMissingClient()
        bridge = Go2RtcXiaomiDiscovery(client)
        with tempfile.TemporaryDirectory(prefix="meowkeeper-phase3c2-profile-list-") as directory:
            service = XiaomiSetupService(
                directory,
                state_path=Path(directory) / "state.json",
                discovery=bridge.discover,
                profile_probe=bridge.profile_probe,
                source_manager=bridge,
            )
            service.refresh()
            result = service.probe_device(service.devices()[0]["internal_id"])

        self.assertEqual(result["setup_stage"], "degraded")
        self.assertEqual(result["profile_count"], 1)
        self.assertEqual(len(client.put_names), 1)

    def test_unconfirmed_region_blocks_quality_and_activation_with_clear_reason(self) -> None:
        client = RegionClient(working_region=None)
        bridge = Go2RtcXiaomiDiscovery(client, region="auto")
        probe_calls: list[tuple[str, ...]] = []
        switch_calls: list[str] = []

        def profile_probe(_device: dict[str, Any], names: tuple[str, ...]) -> list[XiaomiQualityProfile]:
            probe_calls.append(names)
            return []

        def source_switch(url: str, _device: dict[str, Any], _profile: XiaomiQualityProfile) -> dict[str, str]:
            switch_calls.append(url)
            return {"status": "connected"}

        with tempfile.TemporaryDirectory(prefix="meowkeeper-unconfirmed-region-") as directory:
            service = XiaomiSetupService(
                directory,
                state_path=Path(directory) / "state.json",
                discovery=bridge.discover,
                profile_probe=profile_probe,
                source_manager=bridge,
                source_switch=source_switch,
            )
            service.refresh()
            device = service.devices()[0]
            probe = service.probe_device(device["internal_id"])
            activate = service.activate(device["internal_id"])
            refreshed_device = service.devices()[0]

        self.assertFalse(device["source_reference_present"])
        self.assertFalse(refreshed_device["profile_ready"])
        self.assertEqual(probe["action_status"], "blocked")
        self.assertIn("區域", probe["message_zh_tw"])
        self.assertEqual(activate["action_status"], "blocked")
        self.assertIn("SD 影格", activate["message_zh_tw"])
        self.assertEqual(probe_calls, [])
        self.assertEqual(switch_calls, [])

    def test_refresh_restores_active_xiaomi_binding_and_connects_from_saved_profile(self) -> None:
        model = "chuangmi.camera.026c02"
        stream_name = "remembered_xiaomi_stream"
        active_url = f"rtsp://127.0.0.1:8554/{stream_name}"

        class ActiveClient:
            def __init__(self) -> None:
                self.status_names: list[str] = []

            def stream_status(self, name: str) -> dict[str, Any]:
                self.status_names.append(name)
                return {"producers": [{"format_name": "xiaomi/miss", "user_agent": f"CS2 ({model})"}]}

        class ResumeManager:
            def __init__(self) -> None:
                self.client = ActiveClient()
                self.activations: list[tuple[str, str]] = []
                self.verifications: list[tuple[str, str]] = []

            def activate_stream(self, _internal_id: str, name: str, profile: XiaomiQualityProfile) -> str:
                self.activations.append((name, profile.name))
                return active_url

            def verify_active_profile(self, name: str, profile: str) -> XiaomiQualityProfile:
                self.verifications.append((name, profile))
                return XiaomiQualityProfile(profile, 1920, 1080, 20, "hevc", None, True)

        class FakeGo2Rtc:
            rtsp_port = 8554

            @staticmethod
            def status() -> Any:
                return SimpleNamespace(to_dict=lambda: {"status": "running"})

        with tempfile.TemporaryDirectory(prefix="meowkeeper-xiaomi-resume-") as directory:
            root = Path(directory)
            state_path = root / "state.json"
            state_path.write_text(json.dumps({
                "setup_stage": "activating_source",
                "selected_model": model,
                "selected_profile": "subtype=3",
                "selected_stream_name": stream_name,
                "retry_count": 0,
            }), encoding="utf-8")
            manager = ResumeManager()
            switched: list[str] = []
            service = XiaomiSetupService(
                root,
                go2rtc=FakeGo2Rtc(),
                state_path=state_path,
                discovery=lambda: [{
                    "internal_id": "opaque-camera-id",
                    "device_id": "masked-device",
                    "display_name": "測試攝影機",
                    "model": model,
                    "region": "sg",
                    "source_url": "xiaomi://safe-test-reference@192.0.2.10?did=test&model=chuangmi.camera.026c02",
                    "source_reference_present": True,
                    "stream_name": "discovery_default_alias",
                }],
                source_manager=manager,
                source_switch=lambda url, _device, _profile: switched.append(url) or {"status": "running"},
                active_source_url=lambda: active_url,
            )

            refreshed = service.refresh()
            device = service.devices()[0]
            activated = service.activate("opaque-camera-id")

        self.assertEqual(refreshed["setup_stage"], "connected")
        self.assertTrue(device["selected"])
        self.assertEqual(device["stream_name"], stream_name)
        self.assertEqual(device["selected_profile"], "subtype=3")
        self.assertTrue(device["resume_available"])
        self.assertTrue(device["quality_switch_available"])
        self.assertEqual(manager.client.status_names, [stream_name])
        self.assertEqual(manager.activations, [(stream_name, "subtype=3")])
        self.assertEqual(manager.verifications, [(stream_name, "subtype=3")])
        self.assertEqual(switched, [active_url])
        self.assertEqual(activated["action_status"], "connected")
        self.assertEqual(activated["setup_stage"], "connected")

    def test_refresh_does_not_guess_between_two_same_model_cameras(self) -> None:
        model = "chuangmi.camera.026c02"
        active_url = "rtsp://127.0.0.1:8554/ambiguous_stream"

        class ActiveClient:
            def stream_status(self, _name: str) -> dict[str, Any]:
                return {"producers": [{"format_name": "xiaomi/miss", "user_agent": f"CS2 ({model})"}]}

        class Manager:
            client = ActiveClient()

        with tempfile.TemporaryDirectory(prefix="meowkeeper-xiaomi-ambiguous-") as directory:
            service = XiaomiSetupService(
                directory,
                state_path=Path(directory) / "state.json",
                discovery=lambda: [
                    {"internal_id": "camera-a", "model": model, "source_reference_present": True},
                    {"internal_id": "camera-b", "model": model, "source_reference_present": True},
                ],
                source_manager=Manager(),
                active_source_url=lambda: active_url,
            )
            service.refresh()

        self.assertEqual([item["selected"] for item in service.devices()], [False, False])

    def test_delayed_producer_recovers_controls_and_active_probe_preserves_monitor(self) -> None:
        model = "chuangmi.camera.026c02"
        client = SimpleNamespace(stream_status=lambda _name: {"producers": []})
        manager = SimpleNamespace(client=client)
        go2rtc = SimpleNamespace(rtsp_port=8554, status=lambda: SimpleNamespace(to_dict=dict))
        with tempfile.TemporaryDirectory(prefix="meowkeeper-delayed-binding-") as directory:
            source_probe = unittest.mock.Mock(side_effect=AssertionError("不可另開攝影機 SD 串流"))
            source_switch = unittest.mock.Mock(side_effect=AssertionError("不可重啟正在監控的來源"))
            service = XiaomiSetupService(
                directory, state_path=Path(directory) / "state.json", go2rtc=go2rtc,
                discovery=lambda: [{"internal_id": "camera", "model": model, "region": "sg",
                                    "source_reference_present": True, "source_url": "xiaomi://test-reference"}],
                source_manager=manager, profile_probe=source_probe, source_switch=source_switch,
                active_source_url=lambda: "rtsp://127.0.0.1:8554/actual_stream",
                active_source_frame_ready=lambda: True,
            )
            service.refresh()
            with patch("meowkeeper.integrations.xiaomi.setup_service.time.monotonic", return_value=0):
                self.assertFalse(service.devices()[0]["selected"])
            client.stream_status = unittest.mock.Mock(return_value={"producers": [
                {"format_name": "xiaomi/miss", "user_agent": f"CS2 ({model})"}]})
            with patch("meowkeeper.integrations.xiaomi.setup_service.time.monotonic", return_value=1):
                self.assertFalse(service.devices()[0]["selected"])
            client.stream_status.assert_not_called()
            with patch("meowkeeper.integrations.xiaomi.setup_service.time.monotonic", return_value=6):
                recovered = service.devices()[0]
            self.assertTrue(recovered["selected"])
            self.assertTrue(recovered["monitoring_active"])
            self.assertTrue(recovered["quality_switch_available"])
            self.assertEqual(recovered["stream_name"], "actual_stream")
            manager.verify_active_profile = unittest.mock.Mock(side_effect=RuntimeError("FFprobe 逾時"))
            failed_metadata = service.probe_device("camera")
            self.assertEqual(failed_metadata["setup_stage"], "connected")
            self.assertEqual(failed_metadata["action_status"], "metadata_unavailable")
            self.assertIsNone(service.status().get("profile_error_message"))
            source_probe.assert_not_called()
            self.assertEqual(service.activate("camera")["action_status"], "connected")
            source_switch.assert_not_called()
            manager.verify_active_profile = unittest.mock.Mock(return_value=XiaomiQualityProfile(
                "current", 1920, 1080, 20, "hevc", None, True,
            ))
            self.assertEqual(service.probe_device("camera")["setup_stage"], "connected")
            manager.verify_active_profile.assert_called_once_with("actual_stream", "current")
            self.assertEqual(service.devices()[0]["quality"]["width"], 1920)

    def test_selected_camera_does_not_claim_live_frame_without_frame_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="meowkeeper-frame-state-") as directory:
            service = XiaomiSetupService(directory, state_path=Path(directory) / "state.json")
            service._devices = [{"internal_id": "camera", "selected": True, "stream_name": "stream"}]
            self.assertFalse(service.devices()[0]["monitoring_active"])

    def test_template_exposes_loading_and_device_actions(self) -> None:
        template = (ROOT / "src" / "meowkeeper" / "app" / "web" / "templates" / "xiaomi_setup.html").read_text(encoding="utf-8")
        self.assertEqual(template.count('class="panel wizard-step"'), 3)
        self.assertEqual(template.count('id="camera-remote-pair"'), 1)
        self.assertIn('id="xiaomi-connect"', template)
        self.assertIn('id="xiaomi-test"', template)
        self.assertIn('data-camera-role="provider"', template)
        self.assertIn('xiaomi-wizard.js', template)
        self.assertIn("/live", template)

    def test_read_only_appdata_state_falls_back_without_touching_go2rtc_session(self) -> None:
        with tempfile.TemporaryDirectory(prefix="meowkeeper-phase3c2-fallback-") as directory:
            root = Path(directory)
            appdata_state = root / "protected" / "xiaomi-setup.json"
            fallback_state = root / "data" / "state" / "xiaomi-setup.json"
            service = XiaomiSetupService(root, state_path=appdata_state, discovery=list)
            original_write_text = Path.write_text

            def deny_appdata(path: Path, data: str, *args: Any, **kwargs: Any) -> int:
                if path.resolve() == appdata_state.resolve():
                    raise PermissionError("read-only test state")
                return original_write_text(path, data, *args, **kwargs)

            with patch.object(Path, "write_text", new=deny_appdata):
                result = service.refresh()

            self.assertEqual(result["setup_stage"], "device_selection_required")
            self.assertEqual(service.state_path.resolve(), fallback_state.resolve())
            saved = json.loads(fallback_state.read_text(encoding="utf-8"))
            self.assertEqual(saved["setup_stage"], "device_selection_required")


if __name__ == "__main__":
    unittest.main()
