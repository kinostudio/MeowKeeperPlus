"""Opt-in real FFmpeg/go2rtc relay over loopback; all footage and PTZ are synthetic."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from meowkeeper.integrations.go2rtc.client import Go2RtcClient
from meowkeeper.integrations.xiaomi.relay_provider import (
    ControlHandler,
    RelayProvider,
    StreamBridge,
)
from tests.test_camera_connection_workflow import await_job, fixture_service

ROOT=Path(__file__).resolve().parents[1]


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1",0))
        return s.getsockname()[1]


@unittest.skipUnless(os.getenv("RUN_MEOWKEEPER_RELAY_MEDIA")=="1", "明確啟用隔離媒體中繼驗收才啟動 vendor 程序")
class RelayMediaTests(unittest.TestCase):
    def test_two_hosts_native_video_profile_and_verified_position(self):
        ffmpeg=ROOT/"vendor/ffmpeg/bin/ffmpeg.exe"
        go2rtc=ROOT/"vendor/go2rtc/v1.9.14/go2rtc.exe"
        self.assertTrue(ffmpeg.is_file() and go2rtc.is_file())
        with tempfile.TemporaryDirectory(prefix="meowkeeper-relay-media-") as directory:
            root=Path(directory);upstream,api_port,rtsp_port,control_port=[port() for _ in range(4)]
            sample=root/"synthetic.mp4"
            result=subprocess.run([str(ffmpeg),"-nostdin","-hide_banner","-loglevel","error","-f","lavfi","-i",
                "testsrc2=size=320x180:rate=20","-t","3","-c:v","libx264","-pix_fmt","yuv420p","-y",str(sample)],
                capture_output=True,timeout=20,shell=False,check=False)
            self.assertEqual(result.returncode,0)
            # exec emits native H264. go2rtc owns exactly one shared producer.
            command=f'"{ffmpeg}" -nostdin -hide_banner -loglevel error -stream_loop -1 -re -i "{sample}" -c:v copy -an -f mpegts -'
            config=root/"go2rtc.yaml"
            config.write_text(f'api:\n  listen: "127.0.0.1:{api_port}"\nrtsp:\n  listen: "127.0.0.1:{upstream}"\nwebrtc:\n  listen: ""\nstreams:\n  camera_test: {json.dumps("exec:"+command)}\n',encoding="utf-8")
            log_handle=(root/"go2rtc.log").open("wb")
            process=subprocess.Popen([str(go2rtc),"-config",str(config)],stdout=log_handle,stderr=log_handle,shell=False,
                                      creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            provider=None
            try:
                go_client=Go2RtcClient(f"http://127.0.0.1:{api_port}",timeout_seconds=2)
                deadline=time.monotonic()+8
                while time.monotonic()<deadline:
                    try:
                        if go_client.streams():break
                    except (OSError,RuntimeError,ValueError):time.sleep(.1)
                first,_unused=fixture_service(root/"provider")
                first.go2rtc.rtsp_port=upstream
                state={"connected":True,"position":{"angle":40,"elevation":50,"ret":0},"capabilities":{"absolute_move":True}}
                def action(_stream,verb,**kwargs):
                    if verb=="position":state["position"].update(angle=kwargs["angle"],elevation=kwargs["elevation"])
                    return state
                first.ptz_client=SimpleNamespace(xiaomi_ptz_state=lambda *_a,**_k:state,xiaomi_ptz_action=action)
                first.experimental_ptz_enabled=True
                network={"tailscale_ip":"100.64.0.10","devices":[{"device_ref":"peer","online":True,"tailnet_ips":["100.64.0.11"]}]}
                provider=RelayProvider(first,network=lambda:network,firewall=lambda _:True)
                def start_servers(_address):
                    # Only the test transport is substituted; request validation
                    # and the actual TCP/video bytes use the production bridge.
                    provider.config["peer_ip"]="127.0.0.1"
                    bridge=StreamBridge(provider,("127.0.0.1",rtsp_port))
                    control=ThreadingHTTPServer(("127.0.0.1",control_port),ControlHandler)
                    control.daemon_threads=True;control.provider=provider
                    provider.servers=[bridge,control];provider.running=True
                    for server in provider.servers:
                        threading.Thread(target=server.serve_forever,daemon=True).start()
                        provider.started_servers.append(server)
                with patch.object(provider,"_start_servers",side_effect=start_servers):
                    grant=provider.enable("camera_test","peer")
                second,_ready=fixture_service(root/"consumer")
                def jpeg(_url):
                    data=subprocess.run([str(ffmpeg),"-nostdin","-v","error","-rtsp_transport","tcp","-i",
                        f"rtsp://127.0.0.1:{rtsp_port}/camera_test","-frames:v","1","-f","image2pipe","-c:v","mjpeg","pipe:1"],
                        capture_output=True,timeout=12,check=False,shell=False)
                    self.assertEqual(data.returncode, 0, data.stderr.decode("utf-8", errors="replace") + (root/"go2rtc.log").read_text(errors="replace"))
                    return data.returncode==0 and data.stdout.startswith(b"\xff\xd8") and data.stdout.endswith(b"\xff\xd9")
                second._probe_remote_jpeg=jpeg
                pairing = second.pair_remote("camera_test",grant["pairing_code"])
                self.assertTrue(pairing["success"], pairing)
                job=second.connection_jobs.start("camera_test","relay")
                # Media verification is bounded by FFmpeg, so allow longer than
                # the fast unit fixture's helper wait.
                end=time.monotonic()+20
                while second.connection_jobs.snapshot(job["job_id"])["status"]=="running" and time.monotonic()<end:time.sleep(.1)
                self.assertTrue(await_job(second,job)["success"])
                source=second.source_switch.call_args.args[0]
                second.active_source_url=lambda:source
                original_urlopen=urllib.request.urlopen
                def redirected(request,**kwargs):
                    mapped=urllib.request.Request(f"http://127.0.0.1:{control_port}/v1/control",data=request.data,
                        headers=dict(request.header_items()),method=request.method)
                    return original_urlopen(mapped,**kwargs)
                with patch("meowkeeper.integrations.xiaomi.relay_control.urllib.request.urlopen",side_effect=redirected):
                    reading=second.ptz_status("camera_test",refresh=True)
                    self.assertTrue(reading["position_readback_verified"])
                    self.assertTrue(second.ptz_action("camera_test",action="position",angle=40,elevation=53)["success"])
                    self.assertEqual(second.ptz_status("camera_test",refresh=True)["state"]["position"]["elevation"],53)
                    profile=second._relay_control_client("camera_test",second._device_records["camera_test"]).request("profile",profile="subtype=2")
                    self.assertTrue(profile["video_verified"])
                streams=go_client.streams()
                self.assertEqual(len(streams["camera_test"]["producers"]),1)
                self.assertTrue(jpeg(source))
                provider.stop()
                self.assertFalse(provider.running)
                self.assertFalse(provider.secret_path.exists())
            finally:
                if provider is not None:provider.stop()
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
                log_handle.close()
