"""Dashboard-owned WebRTC peers, shared compressed local consumer, no camera dial."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import uuid

from .preview_mse import active_stream, same_origin_websocket, websocket_target


class PacketQueue(asyncio.Queue):
    """Never accumulate unlimited packets when the main loop is under load."""

    async def put(self, item):
        if self.full():
            self.get_nowait()
        self.put_nowait(item)


def validate_offer(sdp):
    if (not isinstance(sdp, str) or len(sdp) > 65536 or "a=recvonly" not in sdp
            or sdp.count("m=video ") != 1
            or any(x in sdp for x in ("a=sendrecv", "a=sendonly", "m=application"))):
        raise ValueError("WebRTC只接受單一唯讀影像。")
    networks = [ipaddress.ip_network(value) for value in
                ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "::1/128", "fc00::/7", "fe80::/10")]
    for line in sdp.splitlines():
        if line.startswith("a=candidate:"):
            parts = line.split()
            if len(parts) < 8:
                raise ValueError("WebRTC候選位址不正確。")
            host = parts[4]
            if re.fullmatch(r"[a-zA-Z0-9-]{1,80}\.local", host):
                continue
            try:
                address = ipaddress.ip_address(host)
                if not any(address in network for network in networks):
                    raise ValueError("not local")
            except ValueError:
                raise ValueError("WebRTC僅允許本機、區網或Tailscale媒體位址。") from None
    return sdp


class SharedRtcSource:
    """One packet-only go2rtc consumer while viewers exist; no decode/encode."""

    def __init__(self, player_factory=None):
        self._lock = asyncio.Lock()
        self._player = None
        self._relay = None
        self._url = None
        self.viewers = 0
        self.player_factory = player_factory

    async def acquire(self, url):
        from aiortc.contrib.media import MediaPlayer, MediaRelay
        async with self._lock:
            if self._player is not None and self._url != url:
                raise ValueError("來源正在切換；請稍後重試。")
            if self._player is None:
                player = await asyncio.to_thread(self.player_factory or MediaPlayer, url, format="rtsp", decode=False,
                                                 options={"rtsp_transport": "tcp", "allowed_media_types": "video+audio"}, timeout=3)
                if player.video is None:
                    # aiortc 1.15 packet passthrough supports H264/VP8, not HEVC.
                    await asyncio.to_thread(player._stop, None)
                    raise ValueError("WebRTC無法原碼傳送此編碼；改用MSE，不啟動轉碼。")
                player.video._queue = PacketQueue(maxsize=32)
                if player.audio is not None:
                    player.audio._queue = PacketQueue(maxsize=32)
                self._player, self._relay, self._url = player, MediaRelay(), url
            self.viewers += 1
            v_sub = self._relay.subscribe(self._player.video, buffered=False)
            a_sub = self._relay.subscribe(self._player.audio, buffered=False) if self._player.audio is not None else None
            return v_sub, a_sub

    async def release(self):
        async with self._lock:
            self.viewers = max(0, self.viewers - 1)
            if not self.viewers and self._player is not None:
                player = self._player
                self._player = self._relay = self._url = None
                # Detach on the event loop before joining the worker. EOF can
                # also call track.stop(); two concurrent _stop calls race the
                # PyAV container close in aiortc 1.15.
                for track in (player.video, player.audio):
                    if track is not None:
                        track._player = None
                        track.stop()
                # Also closes PyAV if nobody requested the first packet.
                await asyncio.to_thread(player._stop, player.video)


async def serve_rtc(websocket, *, core, sessions, go2rtc, slots, source, viewer_tracker=None):
    cookie = websocket.cookies.get("meowkeeper_session")
    session = sessions.authenticate(cookie)
    if session is None:
        await websocket.close(code=4401, reason="登入已失效。")
        return
    if not same_origin_websocket(websocket):
        await websocket.close(code=1008, reason="僅允許同源觀看。")
        return
    if not await slots.acquire():
        await websocket.close(code=1013, reason="觀看人數已達上限。")
        return
    peer = track = None
    acquired = False
    tasks = []
    viewer_id = uuid.uuid4().hex
    viewer_started = False
    try:
        await websocket.accept()
        from aiortc import RTCConfiguration, RTCPeerConnection, RTCRtpSender, RTCSessionDescription
        metadata = await asyncio.to_thread(core.preview_status)
        stream = active_stream(metadata)
        if not stream or websocket_target(getattr(go2rtc, "api_url", None), stream) is None:
            raise ValueError("目前沒有已驗證的本機共享來源。")
        port = getattr(go2rtc, "rtsp_port", None)
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("本機媒體端點尚未準備。")
        request = await asyncio.wait_for(websocket.receive_json(), timeout=5)
        if (not isinstance(request, dict) or set(request) != {"type", "sdp"}
                or request["type"] != "offer" or not isinstance(request["sdp"], str)
                or len(request["sdp"]) > 65536):
            raise ValueError("WebRTC協商格式不正確。")
        sdp = validate_offer(request["sdp"])
        peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        if viewer_tracker is not None:
            client = getattr(websocket, "client", None)

            @peer.on("connectionstatechange")
            async def track_connection_state():
                nonlocal viewer_started
                if peer.connectionState == "connected" and not viewer_started:
                    viewer_tracker.begin_media(
                        viewer_id,
                        session.session_id,
                        getattr(client, "host", None),
                        "webrtc",
                    )
                    viewer_started = True
                elif peer.connectionState in {"failed", "closed", "disconnected"} and viewer_started:
                    viewer_tracker.end_media(viewer_id)
                    viewer_started = False

        transceiver = peer.addTransceiver("video", direction="sendonly")
        transceiver.setCodecPreferences([c for c in RTCRtpSender.getCapabilities("video").codecs
                                        if c.mimeType.lower() == "video/h264"])
        if "m=audio" in sdp:
            audio_transceiver = peer.addTransceiver("audio", direction="sendonly")
            audio_caps = [c for c in RTCRtpSender.getCapabilities("audio").codecs if c.mimeType.lower() in ("audio/opus", "audio/pcmu", "audio/pcma")]
            if audio_caps:
                audio_transceiver.setCodecPreferences(audio_caps)
        await peer.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
        v_track, a_track = await source.acquire(f"rtsp://127.0.0.1:{port}/{stream}")
        acquired = True
        peer.addTrack(v_track)
        if a_track is not None and "m=audio" in sdp:
            peer.addTrack(a_track)
        await peer.setLocalDescription(await peer.createAnswer())
        await websocket.send_json({"type": "answer", "sdp": peer.localDescription.sdp,
                                   "audio_available": a_track is not None and "m=audio" in sdp})

        async def disconnected():
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    return
                if message.get("bytes") or message.get("text"):
                    return  # one offer only; no arbitrary proxy commands

        async def guard():
            while True:
                await asyncio.sleep(1)
                if (sessions.authenticate(cookie) is None or peer.connectionState in {"failed", "closed"}
                        or active_stream(await asyncio.to_thread(core.preview_status)) != stream):
                    return

        tasks = [asyncio.create_task(disconnected()), asyncio.create_task(guard())]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except ValueError as error:
        # Only our fixed messages may reach the browser; SDP/parser errors stay generic.
        text = str(error) if str(error).startswith(("目前沒有", "本機媒體", "WebRTC", "只接受", "來源正在")) else "WebRTC格式無法協商；改用MSE。"
        try:
            await websocket.send_text(json.dumps({"type": "error", "value": text}, ensure_ascii=False))
        except Exception:
            pass
    except Exception:  # noqa: BLE001 - no SDP, credentials or codec internals in logs
        try:
            await websocket.send_json({"type": "error", "value": "WebRTC暫時不可用；改用MSE。"})
        except Exception:  # noqa: BLE001, S110
            pass
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            if peer is not None:
                await peer.close()  # revoke real SRTP media, not only signaling
        finally:
            try:
                if viewer_started:
                    viewer_tracker.end_media(viewer_id)
                if track is not None:
                    track.stop()
                if acquired:
                    await source.release()
            finally:
                await slots.release()
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001, S110
            pass
