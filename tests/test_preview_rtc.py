"""WebRTC實際H264封包轉送與授權撤銷；素材為本機合成影片。"""
import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from meowkeeper.app.web.preview_mse import PreviewMseSlots
from meowkeeper.app.web.preview_rtc import PacketQueue, SharedRtcSource, serve_rtc, validate_offer


class RtcPreviewTests(unittest.TestCase):
    def test_packet_queue_is_bounded(self):
        async def check():
            queue = PacketQueue(maxsize=2)
            for value in range(100):
                await queue.put(value)
            self.assertEqual(queue.qsize(), 2)
            self.assertEqual(await queue.get(), 98)
        asyncio.run(check())

    def test_auth_and_origin_rejected_before_source_or_slots(self):
        async def check():
            for authorized, origin, expected in ((False, "http://localhost", 4401), (True, "http://other", 1008)):
                closed = []
                async def close(closed=closed, **kwargs):
                    closed.append(kwargs["code"])
                socket = SimpleNamespace(cookies={"meowkeeper_session": "fixture"},
                    headers={"host": "localhost", "origin": origin}, url=SimpleNamespace(scheme="ws"), close=close)
                slots = PreviewMseSlots()
                await serve_rtc(socket, core=None, sessions=SimpleNamespace(authenticate=lambda _, allowed=authorized: allowed or None),
                                go2rtc=None, slots=slots, source=None)
                self.assertEqual(closed, [expected])
                self.assertEqual(slots._active, 0)
        asyncio.run(check())

    def test_offer_rejects_public_destinations_and_uploads(self):
        base = "v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 102\r\na=recvonly\r\n"
        self.assertEqual(validate_offer(base), base)
        for value in (base.replace("recvonly", "sendrecv"), base + "m=application 9 UDP/DTLS/SCTP 5000\r\n",
                      base + "a=candidate:1 1 udp 1 8.8.8.8 9999 typ host\r\n"):
            with self.assertRaises(ValueError):
                validate_offer(value)

    def test_two_real_peers_share_packet_source_and_session_revokes_media(self):
        from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
        from aiortc.contrib.media import MediaPlayer

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as scratch:
            clip = Path(scratch) / "h264.ts"
            result = subprocess.run([str(root / "vendor/ffmpeg/bin/ffmpeg.exe"), "-hide_banner", "-loglevel", "error",
                                     "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=20", "-t", "30",
                                     "-c:v", "libx264", "-profile:v", "baseline", "-tune", "zerolatency", "-bf", "0",
                                     "-pix_fmt", "yuv420p", "-g", "20", str(clip)],
                                    capture_output=True, timeout=15, shell=False, check=False)
            self.assertEqual(result.returncode, 0)

            async def scenario():
                opened = []

                def player_factory(url, **kwargs):
                    opened.append(url)
                    self.assertFalse(kwargs["decode"])
                    return MediaPlayer(str(clip), decode=False)

                source = SharedRtcSource(player_factory)
                slots = PreviewMseSlots(4)
                active = {"one", "two"}
                sessions = SimpleNamespace(authenticate=lambda cookie: object() if cookie in active else None)
                core = SimpleNamespace(preview_status=lambda: {
                    "source_is_xiaomi": True, "source_status": "running", "source_connections": 1,
                    "frame_age_seconds": 0, "stream_name": "xiaomi_camera_fixture"})

                class Socket:
                    url = SimpleNamespace(scheme="ws")

                    def __init__(self, cookie, offer):
                        self.cookies = {"meowkeeper_session": cookie}
                        self.headers = {"origin": "http://localhost:18950", "host": "localhost:18950"}
                        self.offer = offer
                        self.answer = asyncio.get_running_loop().create_future()
                        self.messages = asyncio.Queue()

                    async def accept(self):
                        pass

                    async def receive_json(self):
                        return {"type": "offer", "sdp": self.offer}

                    async def receive(self):
                        return await self.messages.get()

                    async def send_json(self, data):
                        if not self.answer.done():
                            self.answer.set_result(data)

                    async def send_text(self, text):
                        if not self.answer.done():
                            self.answer.set_exception(AssertionError("RTC negotiation failed"))

                    async def close(self, **kwargs):
                        pass

                clients, jobs, sockets, frames = [], [], [], []
                try:
                    for cookie in ("one", "two"):
                        client = RTCPeerConnection(RTCConfiguration(iceServers=[]))
                        clients.append(client)
                        received = asyncio.get_running_loop().create_future()

                        @client.on("track")
                        def on_track(track, received=received):
                            async def read():
                                try:
                                    values = [await track.recv() for _ in range(15)]
                                    if not received.done():
                                        received.set_result(values)
                                except Exception as error:  # noqa: BLE001 - propagate receiver failures to the test
                                    if not received.done():
                                        received.set_exception(error)
                            asyncio.create_task(read())

                        client.addTransceiver("video", direction="recvonly")
                        await client.setLocalDescription(await client.createOffer())
                        socket = Socket(cookie, client.localDescription.sdp)
                        sockets.append(socket)
                        jobs.append(asyncio.create_task(serve_rtc(socket, core=core, sessions=sessions,
                            go2rtc=SimpleNamespace(api_url="http://127.0.0.1:1984", rtsp_port=8554), slots=slots, source=source)))
                        answer = await asyncio.wait_for(socket.answer, 8)
                        await client.setRemoteDescription(RTCSessionDescription(**answer))
                        frames.append(received)
                    try:
                        received_frames = await asyncio.wait_for(asyncio.gather(*frames), 8)
                    except asyncio.TimeoutError:
                        diagnostics = []
                        for client in clients:
                            stats = await client.getStats()
                            diagnostics.append({"state": client.connectionState,
                                "packets": [getattr(v, "packetsReceived", None) for v in stats.values() if v.type == "inbound-rtp"],
                                "codecs": [v.mimeType for r in client.getReceivers() for v in r._RTCRtpReceiver__codecs.values()]})
                        self.fail(f"WebRTC影格逾時：{diagnostics}")
                    for values in received_frames:
                        self.assertEqual((values[-1].width, values[-1].height), (320, 180))
                    self.assertEqual(len(opened), 1)
                    self.assertEqual(source.viewers, 2)
                    active.remove("one")
                    await asyncio.wait_for(jobs[0], 4)
                    self.assertEqual(source.viewers, 1)
                    self.assertFalse(jobs[1].done())
                    await sockets[1].messages.put({"type": "websocket.disconnect"})
                    await asyncio.wait_for(jobs[1], 4)
                    self.assertEqual(source.viewers, 0)
                    self.assertIsNone(source._player)
                    self.assertEqual(slots._active, 0)
                finally:
                    for client in clients:
                        await asyncio.wait_for(client.close(), 3)
                    for job in jobs:
                        job.cancel()
                    await asyncio.gather(*jobs, return_exceptions=True)

            with patch("aioice.ice.get_host_addresses", return_value=["127.0.0.1"]):
                asyncio.run(asyncio.wait_for(scenario(), 30))
