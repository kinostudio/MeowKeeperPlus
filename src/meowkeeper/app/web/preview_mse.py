"""Authenticated, source-pinned MSE/WebSocket proxy for live preview."""

from __future__ import annotations

import asyncio
import json
import math
import re
import uuid
from collections.abc import Mapping
from urllib.parse import quote, urlsplit

ALLOWED_CODECS = frozenset(
    {
        "avc1.640029",
        "avc1.64002A",
        "avc1.640033",
        "avc1.4D401F",
        "avc1.42E01E",
        "hvc1.1.6.L153.B0",
    }
)
STREAM_NAME = re.compile(r"^xiaomi_camera_[A-Za-z0-9_-]{1,64}$")
MIME_CODEC = re.compile(
    r"^(?:avc1\.[0-9A-Fa-f]{6}|hvc1\.[0-9]+\.[0-9]+\.L[0-9]+\.B[0-9]+|"
    r"mp4a\.40\.[0-9]+|opus|flac)$"
)
MSE_MIME = re.compile(r'^video/mp4;\s*codecs="([A-Za-z0-9., ]{1,256})"$')


class PreviewMseError(ValueError):
    """A safe, user-presentable failure category for the preview proxy."""


class PreviewMseSlots:
    """Bound browser MSE consumers without changing the camera producer."""

    def __init__(self, maximum: int = 4) -> None:
        self.maximum = maximum
        self._active = 0
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        async with self._lock:
            if self._active >= self.maximum:
                return False
            self._active += 1
            return True

    async def release(self) -> None:
        async with self._lock:
            self._active = max(0, self._active - 1)


def validate_codec_offer(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise PreviewMseError("瀏覽器未提供有效的連續播放格式。")
    codecs = value.split(",")
    if not 1 <= len(codecs) <= len(ALLOWED_CODECS):
        raise PreviewMseError("瀏覽器提供的播放格式數量不正確。")
    if any(codec not in ALLOWED_CODECS for codec in codecs) or len(set(codecs)) != len(codecs):
        raise PreviewMseError("瀏覽器提供了未允許的播放格式。")
    return ",".join(codecs)


def safe_mse_mime(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = MSE_MIME.fullmatch(value)
    if match is None:
        return None
    codecs = [codec.strip() for codec in match.group(1).split(",")]
    if not codecs or any(MIME_CODEC.fullmatch(codec) is None for codec in codecs):
        return None
    return value


def websocket_target(api_url: object, stream_name: object) -> tuple[str, str] | None:
    """Build a fixed localhost upstream URL from a trusted runtime stream id."""

    if not isinstance(stream_name, str) or STREAM_NAME.fullmatch(stream_name) is None:
        return None
    if not isinstance(api_url, str):
        return None
    try:
        parts = urlsplit(api_url)
        if (
            parts.scheme != "http"
            or parts.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parts.username
            or parts.password
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
            or parts.port is None
            or not 1 <= parts.port <= 65535
        ):
            return None
    except ValueError:
        return None
    host = "[::1]" if parts.hostname == "::1" else parts.hostname
    origin = f"http://{host}:{parts.port}"
    return f"ws://{host}:{parts.port}/api/ws?src={quote(stream_name, safe='')}", origin


def same_origin_websocket(websocket: object) -> bool:
    headers = getattr(websocket, "headers", {})
    origin = str(headers.get("origin", ""))
    request_host = str(headers.get("host", ""))
    if not origin or not request_host:
        return False
    try:
        origin_parts = urlsplit(origin)
        host_parts = urlsplit(f"//{request_host}")
        scheme = str(getattr(getattr(websocket, "url", None), "scheme", ""))
        expected_scheme = "https" if scheme == "wss" else "http" if scheme == "ws" else ""
        origin_port = origin_parts.port or (443 if origin_parts.scheme == "https" else 80)
        host_port = host_parts.port or (443 if expected_scheme == "https" else 80)
        return (
            origin_parts.scheme == expected_scheme
            and bool(origin_parts.hostname)
            and origin_parts.hostname.casefold() == (host_parts.hostname or "").casefold()
            and origin_port == host_port
            and origin_parts.path in {"", "/"}
            and not origin_parts.query
            and not origin_parts.fragment
            and not origin_parts.username
            and not origin_parts.password
            and not host_parts.username
            and not host_parts.password
            and not host_parts.path
            and not host_parts.query
            and not host_parts.fragment
        )
    except (TypeError, ValueError):
        return False


def active_stream(metadata: object) -> str | None:
    if not isinstance(metadata, Mapping):
        return None
    if metadata.get("source_is_xiaomi") is not True:
        return None
    if metadata.get("source_status") not in {"running", "streaming", "connected"}:
        return None
    try:
        age = float(metadata.get("frame_age_seconds"))
        connections = int(metadata.get("source_connections"))
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(age) or age < 0 or age > 15 or connections < 1:
        return None
    stream = metadata.get("stream_name")
    return stream if isinstance(stream, str) and STREAM_NAME.fullmatch(stream) else None


async def proxy_mse_preview(
    websocket: object,
    *,
    core: object,
    sessions: object,
    go2rtc: object,
    slots: PreviewMseSlots,
    viewer_tracker: object | None = None,
    transcode: object | None = None,
) -> None:
    """Serve one authenticated MSE consumer; never accepts a browser URL/source."""

    cookie = websocket.cookies.get("meowkeeper_session")
    session = sessions.authenticate(cookie)
    if session is None:
        await websocket.close(code=4401, reason="登入已失效，請重新登入。")
        return
    if not same_origin_websocket(websocket):
        await websocket.close(code=1008, reason="僅允許目前 Dashboard 同源連線。")
        return
    if go2rtc is None:
        await websocket.close(code=1013, reason="目前沒有可供連續播放的本機 go2rtc 來源。")
        return

    try:
        metadata = await asyncio.to_thread(core.preview_status)
        stream = active_stream(metadata)
        api_url = getattr(go2rtc, "api_url", None)
    except Exception:  # noqa: BLE001 - preview is optional and errors stay generic
        await websocket.close(code=1013, reason="連續播放狀態暫時無法確認；可使用節能預覽。")
        return
    target = websocket_target(api_url, stream)
    if stream is None or target is None:
        await websocket.close(code=1013, reason="目前沒有可供連續播放的已驗證小米影格；已保留節能預覽。")
        return
    if not await slots.acquire():
        await websocket.close(code=1013, reason="連續播放觀看人數已達上限；可改用節能預覽。")
        return

    upstream = None
    tasks: set[asyncio.Task[object]] = set()
    viewer_id = uuid.uuid4().hex
    viewer_started = False
    compatibility_acquired = False
    try:
        await websocket.accept()
        from websockets.asyncio.client import connect

        upstream_url, upstream_origin = target
        try:
            message = await asyncio.wait_for(websocket.receive(), timeout=5)
            payload = message.get("text") if isinstance(message, Mapping) else None
            if not isinstance(payload, str) or len(payload.encode("utf-8")) > 512:
                raise PreviewMseError("連續播放初始化訊息格式或大小不正確。")
            try:
                request = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise PreviewMseError("連續播放初始化訊息格式不正確。") from exc
            if not isinstance(request, Mapping) or request.get("type") != "mse":
                raise PreviewMseError("連續播放初始化訊息不正確。")
            codecs = validate_codec_offer(request.get("value"))
            compatibility = request.get("compatibility", False)
            if type(compatibility) is not bool:
                raise PreviewMseError("相容播放選項無效。")
            if compatibility:
                codecs = ",".join(c for c in codecs.split(",") if c.startswith("avc1."))
                if transcode is None or not codecs:
                    raise PreviewMseError("此環境沒有可用的 H.264 相容播放。")
                compat_stream = await transcode.acquire(api_url, getattr(go2rtc, "rtsp_port", None), stream, metadata.get("source_epoch"))
                compatibility_acquired = True
                upstream_url, upstream_origin = websocket_target(api_url, compat_stream)
            upstream = await connect(
                upstream_url,
                origin=upstream_origin,
                proxy=None,
                compression=None,
                open_timeout=5,
                close_timeout=3,
                max_size=8 * 1024 * 1024,
                ping_interval=20,
                ping_timeout=20,
            )
            await upstream.send(json.dumps({"type": "mse", "value": codecs}, separators=(",", ":")))
            handshake = await asyncio.wait_for(upstream.recv(), timeout=10)
            if not isinstance(handshake, str):
                raise PreviewMseError("影音伺服器未回傳連續播放格式。")
            try:
                response = json.loads(handshake)
            except json.JSONDecodeError as exc:
                raise PreviewMseError("影音伺服器回應格式不正確。") from exc
            if not isinstance(response, Mapping) or response.get("type") != "mse" or safe_mse_mime(response.get("value")) is None:
                raise PreviewMseError("目前攝影機格式或瀏覽器不支援連續播放。")
            reply = {"type": "mse", "value": response["value"]}
            if compatibility_acquired:
                reply["transcode"] = transcode.snapshot()
            await websocket.send_text(json.dumps(reply, separators=(",", ":")))
        except PreviewMseError as exc:
            await websocket.send_text(json.dumps({"type": "error", "value": str(exc)}, ensure_ascii=False))
            await websocket.close(code=1008, reason=str(exc))
            return
        except Exception:  # noqa: BLE001 - do not disclose local paths or go2rtc logs
            await websocket.close(code=1013, reason="連續播放來源目前無法使用；已保留節能預覽。")
            return

        async def upstream_to_browser() -> None:
            nonlocal viewer_started
            while True:
                message = await upstream.recv()
                if isinstance(message, str):
                    # go2rtc should send only the initial JSON negotiation; do not
                    # leak arbitrary log or source text to the browser.
                    continue
                if viewer_tracker is not None and not viewer_started:
                    client = getattr(websocket, "client", None)
                    viewer_tracker.begin_media(
                        viewer_id,
                        session.session_id,
                        getattr(client, "host", None),
                        "mse",
                    )
                    viewer_started = True
                if viewer_started:
                    viewer_tracker.touch_media(viewer_id)
                await websocket.send_bytes(message)

        async def browser_disconnect() -> None:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    return
                if message.get("bytes") or message.get("text"):
                    await websocket.close(code=1008, reason="連續播放不接受控制指令。")
                    return

        async def session_guard() -> None:
            while True:
                await asyncio.sleep(5)
                if sessions.authenticate(cookie) is None:
                    await websocket.close(code=4401, reason="登入已失效，請重新登入。")
                    return
                latest = await asyncio.to_thread(core.preview_status)
                if (active_stream(latest) != stream or latest.get("source_epoch") != metadata.get("source_epoch")):
                    await websocket.close(code=1013, reason="來源影格已停止或改變；連續播放已關閉。")
                    return

        tasks = {
            asyncio.create_task(upstream_to_browser()),
            asyncio.create_task(browser_disconnect()),
            asyncio.create_task(session_guard()),
        }
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, *pending, return_exceptions=True)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if upstream is not None:
            try:
                await upstream.close()
            except Exception:  # noqa: BLE001, S110 - cleanup is best effort after disconnect
                pass
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001, S110 - client may already have disconnected
            pass
        if viewer_started:
            viewer_tracker.end_media(viewer_id)
        if compatibility_acquired:
            await transcode.release()
        await slots.release()
