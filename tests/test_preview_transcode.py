"""Lazy shared compatibility preview: no camera credentials or physical devices."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from meowkeeper.app.web.preview_mse import PreviewMseSlots, proxy_mse_preview, websocket_target
from meowkeeper.app.web.preview_transcode import (
    SharedPreviewTranscode,
    select_encoder,
    transcode_command,
)
from meowkeeper.integrations.go2rtc.client import Go2RtcClient
from tests.test_preview_mse import _FakeUpstream, _FakeWebSocket
from tests.test_relay_loopback_media import port

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = str(ROOT/"vendor/ffmpeg/bin/ffmpeg.exe")


class TranscodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_refcounts_no_viewers_no_work_shared_source_and_last_release(self):
        client, probe = Mock(), Mock(return_value="libx264")
        pool=SharedPreviewTranscode(ffmpeg=FFMPEG, encoder_probe=probe, client_factory=lambda *_a,**_k:client)
        self.assertFalse(pool.snapshot()["active"])
        probe.assert_not_called();client.request_json.assert_not_called()
        args=("http://127.0.0.1:1984",8554,"xiaomi_camera_test",1)
        first,second=await asyncio.gather(pool.acquire(*args),pool.acquire(*args))
        self.assertEqual(first,second);probe.assert_called_once();self.assertEqual(client.request_json.call_count,1)
        self.assertEqual(pool.viewers,2)
        with self.assertRaises(ValueError):await pool.acquire(*args[:-1],2)
        await pool.release();self.assertEqual(client.request_json.call_count,1)
        await pool.release();self.assertEqual(client.request_json.call_args.kwargs["method"],"DELETE")
        self.assertFalse(pool.snapshot()["active"])
        await pool.acquire(*args);self.assertEqual(probe.call_count,1)
        await pool.close();self.assertEqual(pool.viewers,0)
        with self.assertRaises(ValueError):await pool.acquire(*args)

    async def test_compatibility_proxy_uses_pool_and_preserves_login_guard(self):
        pool=SimpleNamespace(acquire=Mock(),release=Mock(),snapshot=lambda:{"encoder":"libx264"})
        async def acquire(*_args):return "xiaomi_camera_compat_test"
        async def release():pool.release_count+=1
        pool.acquire=acquire;pool.release=release;pool.release_count=0
        core=SimpleNamespace(preview_status=lambda:{"source_is_xiaomi":True,"source_status":"running",
             "frame_age_seconds":0,"source_connections":1,"stream_name":"xiaomi_camera_test","source_epoch":1})
        sessions=SimpleNamespace(authenticate=lambda c:SimpleNamespace(session_id="test") if c else None)
        upstream=_FakeUpstream();upstream.messages[0]=json.dumps({"type":"mse","value":'video/mp4; codecs="avc1.42C01F"'})
        socket=_FakeWebSocket({"type":"mse","value":"avc1.42E01E,mp4a.40.2,opus","compatibility":True})
        async def connect(url,**_kwargs):
            self.assertIn("xiaomi_camera_compat_test",url);return upstream
        with patch("websockets.asyncio.client.connect",side_effect=connect):
            await proxy_mse_preview(socket,core=core,sessions=sessions,go2rtc=SimpleNamespace(api_url="http://127.0.0.1:1984",rtsp_port=8554),slots=PreviewMseSlots(),transcode=pool)
        self.assertEqual(pool.release_count,1)
        self.assertEqual(upstream.sent[0]["value"], "avc1.42E01E,mp4a.40.2")
        self.assertEqual(json.loads(socket.texts[0])["transcode"]["encoder"],"libx264")
        self.assertEqual(socket.binary,[b"fragment"])
        socket=_FakeWebSocket();socket.cookies={}
        await proxy_mse_preview(socket,core=core,sessions=sessions,go2rtc=None,slots=PreviewMseSlots(),transcode=pool)
        self.assertFalse(socket.accepted);self.assertEqual(pool.release_count,1)

    def test_encoder_probe_falls_back_and_command_has_optional_bounded_aac_audio(self):
        with patch("subprocess.run",return_value=SimpleNamespace(returncode=1)) as run:
            self.assertEqual(select_encoder(FFMPEG),"libx264")
            self.assertEqual(run.call_count,2)
            self.assertFalse(run.call_args.kwargs["shell"])
        command=" ".join(transcode_command(FFMPEG,8554,"xiaomi_camera_test","libx264"))
        for required in ("rtsp://127.0.0.1:8554/xiaomi_camera_test","0:a:0?","aac","64k","yuv420p","mpegts","ultrafast","fps=15"):
            self.assertIn(required,command)
        self.assertNotIn("-an", command)
        for stream in ("../api/config","rtsp://camera/private","xiaomi_camera_a&other"):
            with self.assertRaises(ValueError):transcode_command(FFMPEG,8554,stream,"libx264")


@unittest.skipUnless(os.getenv("RUN_MEOWKEEPER_RELAY_MEDIA")=="1", "需明確啟用隔離 vendor 媒體程序")
class RealTranscodeTests(unittest.TestCase):
    def test_hevc_to_h264_two_viewers_share_encoder_late_join_and_cleanup(self):
        with tempfile.TemporaryDirectory(prefix="meowkeeper-h264-") as directory:
            root=Path(directory);api_port,rtsp_port=port(),port()
            sample=root/"synthetic-hevc.mp4"
            built=subprocess.run([FFMPEG,"-nostdin","-v","error","-f","lavfi","-i","testsrc2=size=320x180:rate=20",
                "-f","lavfi","-i","sine=frequency=440:sample_rate=48000",
                "-t","3","-c:v","libx265","-x265-params","pools=1:frame-threads=1:log-level=error",
                "-c:a","aac","-ac","1","-y",str(sample)],
                capture_output=True,timeout=20,shell=False,check=False)
            self.assertEqual(built.returncode,0)
            command=f'"{FFMPEG}" -nostdin -v error -stream_loop -1 -re -i "{sample}" -c copy -f mpegts -'
            config=root/"go2rtc.yaml"
            config.write_text(f'api:\n  listen: "127.0.0.1:{api_port}"\nrtsp:\n  listen: "127.0.0.1:{rtsp_port}"\nwebrtc:\n  listen: ""\nstreams:\n  xiaomi_camera_test: {json.dumps("exec:"+command)}\n',encoding="utf-8")
            log=(root/"go.log").open("wb")
            process=subprocess.Popen([str(ROOT/"vendor/go2rtc/v1.9.14/go2rtc.exe"),"-config",str(config)],stdout=log,stderr=log,shell=False,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            try:
                client=Go2RtcClient(f"http://127.0.0.1:{api_port}")
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    try:
                        if client.streams():break
                    except (OSError,RuntimeError):time.sleep(.1)
                async def scenario():
                    from websockets.asyncio.client import connect
                    pool=SharedPreviewTranscode(ffmpeg=FFMPEG,encoder_probe=lambda _p:"libx264")
                    first=await pool.acquire(client.base_url,rtsp_port,"xiaomi_camera_test",1)
                    url,origin=websocket_target(client.base_url,first)
                    async def begin():
                        ws=await connect(url,origin=origin,proxy=None)
                        await ws.send(json.dumps({"type":"mse","value":"avc1.640029,avc1.42E01E,mp4a.40.2"}))
                        response=json.loads(await asyncio.wait_for(ws.recv(),10))
                        self.assertEqual(response.get("type"),"mse",(response,pool.producer.diagnostic.decode(errors="replace"),(root/"go.log").read_text(errors="replace")))
                        self.assertIn("avc1.",response["value"])
                        self.assertIn("mp4a.40.2",response["value"], "compatibility stream must retain AAC audio")
                        return ws
                    a=b=None
                    try:
                        a=await begin()
                        payload=bytearray()
                        # Init/video fragments can precede the first AAC chunk.
                        # Collect a bounded real-time window, not just an init header.
                        sample_until=asyncio.get_running_loop().time()+1.2
                        while len(payload)<10000 or asyncio.get_running_loop().time()<sample_until:
                            fragment=await asyncio.wait_for(a.recv(),5)
                            if isinstance(fragment,bytes):payload.extend(fragment)
                            self.assertLess(len(payload),4*1024*1024)
                        second=await pool.acquire(client.base_url,rtsp_port,"xiaomi_camera_test",1)
                        self.assertEqual(first,second)
                        b=await begin();self.assertIsInstance(await asyncio.wait_for(b.recv(),5),bytes)
                        streams=await asyncio.to_thread(client.streams)
                        self.assertEqual(len(streams["xiaomi_camera_test"]["producers"]),1)
                        self.assertEqual(len(streams[first]["producers"]),1)
                        decoded=await asyncio.to_thread(subprocess.run,[FFMPEG,"-nostdin","-v","error","-i","pipe:0","-frames:v","1","-f","image2pipe","-c:v","mjpeg","pipe:1"],input=bytes(payload),capture_output=True,timeout=10,shell=False,check=False)
                        self.assertEqual(decoded.returncode,0,decoded.stderr.decode(errors="replace"));self.assertTrue(decoded.stdout.startswith(b"\xff\xd8"))
                        audio=await asyncio.to_thread(subprocess.run,[FFMPEG,"-nostdin","-v","error","-i","pipe:0","-map","0:a:0","-t","0.1","-f","s16le","-c:a","pcm_s16le","pipe:1"],input=bytes(payload),capture_output=True,timeout=10,shell=False,check=False)
                        self.assertEqual(audio.returncode,0,audio.stderr.decode(errors="replace"))
                        self.assertGreater(len(audio.stdout),1000)
                        self.assertTrue(any(audio.stdout), "decoded synthetic tone must contain non-silent PCM samples")
                        await a.close();await pool.release();self.assertTrue(pool.snapshot()["active"])
                        await b.close();await pool.release();self.assertFalse(pool.snapshot()["active"])
                        self.assertNotIn(first,await asyncio.to_thread(client.streams))
                    finally:
                        if a:await a.close()
                        if b:await b.close()
                        await pool.close()
                asyncio.run(asyncio.wait_for(scenario(),30))
                if os.getenv("MEOWKEEPER_PLAYWRIGHT"):
                    import uvicorn
                    from fastapi.responses import HTMLResponse

                    from meowkeeper.app.web.api import create_app
                    from meowkeeper.app.web.session import SessionManager
                    from meowkeeper.app.web.state import CoreStateFacade

                    sessions=SessionManager();sessions.configure_admin("fixture-admin","fixture-password-only")
                    facade=CoreStateFacade()
                    facade.preview_status=Mock(return_value={"source_is_xiaomi":True,"source_status":"running",
                        "frame_age_seconds":0,"source_connections":1,"stream_name":"xiaomi_camera_test","source_epoch":1,
                        "original_source":{"video_codec":"hevc"}})
                    integration=SimpleNamespace(go2rtc=SimpleNamespace(api_url=client.base_url,rtsp_port=rtsp_port),devices=list)
                    app=create_app(facade=facade,session_manager=sessions,integrations=integration)
                    if app.state.resource_recorder is not None:
                        app.state.resource_recorder.start=lambda:None
                        app.state.resource_recorder.stop=lambda:None
                    app.state.preview_transcode=SharedPreviewTranscode(ffmpeg=FFMPEG,encoder_probe=lambda _p:"libx264")
                    @app.get("/isolated-h264-browser")
                    def browser_fixture():
                        return HTMLResponse('''<!doctype html><html><body><p>SYNTHETIC HEVC COMPATIBILITY FIXTURE</p>
                        <div class="preview-stage"><img id="frame"><div id="empty"></div></div><p id="note"></p>
                        <script src="/static/js/mse-player.js"></script><script src="/static/js/shared-preview.js"></script>
                        <script>window.MeowKeeperPreview.mount({image:document.getElementById('frame'),empty:document.getElementById('empty'),note:document.getElementById('note'),defaultMode:'smooth'});</script></body></html>''')
                    browser_port=port()
                    server=uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=browser_port,log_level="error"))
                    thread=threading.Thread(target=server.run,daemon=True);thread.start()
                    try:
                        end=time.monotonic()+5
                        while not server.started and time.monotonic()<end:time.sleep(.05)
                        self.assertTrue(server.started)
                        artifact=ROOT/"reports/camera-ui-redesign-2026-10-18/h264-browser.json"
                        artifact.parent.mkdir(parents=True, exist_ok=True)
                        browser=subprocess.run([str(ROOT/"vendor/javascript/node.exe"),str(ROOT/"tests/browser/transcode_browser.cjs"),
                            f"http://127.0.0.1:{browser_port}",str(artifact)],capture_output=True,timeout=65,shell=False,check=False)
                        self.assertEqual(browser.returncode,0,browser.stderr.decode(errors="replace"))
                        navigation=subprocess.run([str(ROOT/"vendor/javascript/node.exe"),str(ROOT/"tests/browser/monitor_navigation.cjs"),
                            f"http://127.0.0.1:{browser_port}"],capture_output=True,timeout=75,shell=False,check=False)
                        self.assertEqual(navigation.returncode,0,navigation.stderr.decode(errors="replace"))
                        print(navigation.stdout.decode(errors="replace"))
                        end=time.monotonic()+3
                        while app.state.preview_transcode.viewers and time.monotonic()<end:time.sleep(.1)
                        self.assertEqual(app.state.preview_transcode.viewers,0)
                    finally:
                        server.should_exit=True;thread.join(timeout=10)
            finally:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
                log.close()
