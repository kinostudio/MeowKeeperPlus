"""One lazy H.264 compatibility producer, consuming only the local go2rtc stream."""
from __future__ import annotations

import asyncio
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from meowkeeper.integrations.go2rtc.client import Go2RtcClient
from meowkeeper.runtime import bundled_binary

from .preview_mse import websocket_target


def select_encoder(ffmpeg: str) -> str:
    """Probe actual encoder initialization, not the GPU name or encoder listing."""
    for encoder in ("h264_qsv", "h264_nvenc"):
        try:
            result = subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=black:s=1920x1080:r=20", "-frames:v", "2",
                "-an", "-c:v", encoder, "-f", "null", "-"], capture_output=True,
                timeout=3, shell=False, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode == 0:
                return encoder
        except (OSError, subprocess.TimeoutExpired):
            continue
    return "libx264"


def transcode_command(ffmpeg: str, port: int, stream: str, encoder: str) -> list[str]:
    if (websocket_target("http://127.0.0.1:1984", stream) is None or type(port) is not int
            or not 1 <= port <= 65535 or encoder not in {"h264_qsv", "h264_nvenc", "libx264"}
            or not Path(ffmpeg).is_file()):
        raise ValueError("相容播放的本機來源或編碼器無效。")
    hardware = encoder != "libx264"
    width, height, fps = (1920, 1080, 20) if hardware else (1280, 720, 15)
    scale = f"scale='min({width},iw)':'min({height},ih)':force_original_aspect_ratio=decrease:force_divisible_by=2"
    args = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-threads", "2",
            "-rtsp_transport", "tcp", "-timeout", "5000000", "-i", f"rtsp://127.0.0.1:{port}/{stream}",
            "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", "-vf", scale+f",fps={fps},format=yuv420p",
            "-c:v", encoder, "-threads", "2", "-g", str(fps), "-bf", "0", "-b:v", "2500k",
            "-maxrate", "3000k", "-bufsize", "3000k",
            "-c:a", "aac", "-b:a", "64k", "-ac", "1", "-ar", "48000"]
    if not hardware:
        args += ["-preset", "ultrafast", "-tune", "zerolatency"]
    args += ["-f", "mpegts", "-"]
    return args


class TranscodeProducer:
    """One private loopback MPEG-TS response, one owned FFmpeg, bounded socket buffers."""
    def __init__(self, args):
        self.args, self.process = args, None
        self.lock = threading.RLock()
        self.running = True
        self.exit_code = None
        self.diagnostic = b""
        self.stderr_thread = None
        self.path = "/"+uuid.uuid4().hex+".ts"
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                with owner.lock:
                    if self.path != owner.path or self.client_address[0] != "127.0.0.1" or not owner.running:
                        self.send_error(404);return
                    if owner.process is not None:
                        self.send_error(503);return
                    try:
                        child = subprocess.Popen(owner.args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, shell=False, creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
                    except OSError:
                        self.send_error(503);return
                    owner.process = child
                    def drain():
                        try:
                            while True:
                                chunk = child.stderr.read(512)
                                if not chunk:
                                    return
                                owner.diagnostic = (owner.diagnostic + chunk)[-2048:]
                        except (OSError, ValueError):
                            return
                    owner.stderr_thread = threading.Thread(target=drain,daemon=True,name="preview-h264-diagnostic")
                    owner.stderr_thread.start()
                try:
                    self.connection.settimeout(5)
                    self.send_response(200)
                    self.send_header("Content-Type","video/MP2T")
                    self.send_header("Cache-Control","no-store")
                    self.end_headers()
                    while owner.running:
                        data = child.stdout.read1(65536)
                        if not data:
                            break
                        self.wfile.write(data)
                except (OSError, ValueError):
                    pass
                finally:
                    owner.stop_child(child)

        self.server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever,kwargs={"poll_interval":.05},daemon=True,name="preview-h264")
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}{self.path}"

    def stop_child(self, child):
        with self.lock:
            if self.process is not child:
                return
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill();child.wait(timeout=3)
            self.exit_code = child.returncode
            if self.stderr_thread:
                self.stderr_thread.join(timeout=1)
            if child.stderr:
                child.stderr.close()
            if child.stdout:
                child.stdout.close()
            self.process = None

    def close(self):
        self.running = False
        with self.lock:
            child = self.process
        if child is not None:
            self.stop_child(child)
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)


class SharedPreviewTranscode:
    def __init__(self, *, ffmpeg=None, encoder_probe=select_encoder, client_factory=Go2RtcClient):
        self.ffmpeg, self.encoder_probe, self.client_factory = ffmpeg, encoder_probe, client_factory
        self.encoder = None
        self.lock = asyncio.Lock()
        self.key = None
        self.stream = None
        self.client = None
        self.producer = None
        self.viewers = 0
        self.closed = False

    async def acquire(self, api_url, port, stream, epoch=None):
        if websocket_target(api_url, stream) is None or type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("目前沒有可用的本機共享來源。")
        key = (api_url, port, stream, epoch)
        async with self.lock:
            if self.closed:
                raise ValueError("相容播放正在關閉。")
            if self.viewers and self.key != key:
                raise ValueError("來源正在切換；相容播放等待舊觀看者離開。")
            if self.stream is None:
                ffmpeg = self.ffmpeg or bundled_binary("ffmpeg")
                if not ffmpeg or not Path(ffmpeg).is_file():
                    raise ValueError("找不到內附 FFmpeg，無法啟動相容播放。")
                if self.encoder is None:
                    self.encoder = await asyncio.to_thread(self.encoder_probe, ffmpeg)
                name = "xiaomi_camera_compat_" + uuid.uuid4().hex
                client = self.client_factory(api_url, timeout_seconds=5)
                command = transcode_command(ffmpeg, port, stream, self.encoder)
                producer = TranscodeProducer(command)
                try:
                    registration = asyncio.create_task(asyncio.to_thread(client.request_json, "/api/streams", method="PATCH", params={"name":name,"src":producer.url}))
                    try:
                        await asyncio.shield(registration)
                    except asyncio.CancelledError:
                        await registration
                        await self._remove(client, name, producer)
                        raise
                except Exception:
                    # A transport timeout can happen after go2rtc registered it.
                    await self._remove(client, name, producer)
                    raise
                self.key, self.stream, self.client = key, name, client
                self.producer = producer
            self.viewers += 1
            return self.stream

    @staticmethod
    async def _remove(client, stream, producer):
        await asyncio.to_thread(producer.close)
        try:
            await asyncio.to_thread(client.request_json, "/api/streams", method="DELETE", params={"src":stream})
        except (OSError, RuntimeError, ValueError):
            # Never fall back to stopping go2rtc or unrelated FFmpeg processes.
            pass

    async def release(self):
        async with self.lock:
            self.viewers = max(0, self.viewers-1)
            if not self.viewers and self.stream is not None:
                await self._remove(self.client, self.stream, self.producer)
                self.key = self.stream = self.client = self.producer = None

    async def close(self):
        async with self.lock:
            self.closed = True
            if self.stream is not None:
                await self._remove(self.client, self.stream, self.producer)
            self.key = self.stream = self.client = self.producer = None
            self.viewers = 0

    def snapshot(self):
        hardware = self.encoder in {"h264_qsv", "h264_nvenc"}
        return {"active":self.viewers>0, "viewers":self.viewers, "encoder":self.encoder,
                "maximum_width":1920 if hardware else 1280, "maximum_fps":20 if hardware else 15,
                "message_zh_tw":"共用硬體 H.264 相容轉碼" if hardware else "共用 CPU H.264 相容轉碼，保守上限 720p／15 FPS"}
