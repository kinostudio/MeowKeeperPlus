"""C701 Tailnet-only control bridge; no Xiaomi secret is logged or returned.

Run with the bundled Python runtime. The official go2rtc API remains bound to
127.0.0.1; this bridge exposes only allow-listed, bearer-authenticated actions.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import hmac
import ipaddress
import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

API = "http://127.0.0.1:21984"
PROFILES = {"subtype=1", "subtype=2", "subtype=3", "subtype=auto"}
MODEL = "chuangmi.camera.079ae2"
STREAM = "c701_sd"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _unseal(path: Path) -> dict[str, str]:
    source = path.read_bytes()
    buffer = ctypes.create_string_buffer(source)
    input_blob = _Blob(len(source), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output_blob = _Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    if not crypt.CryptUnprotectData(ctypes.byref(input_blob), None, None, None, None, 1, ctypes.byref(output_blob)):
        raise RuntimeError("本機配對憑證無法解密。")
    try:
        payload = json.loads(ctypes.string_at(output_blob.pbData, output_blob.cbData))
    finally:
        kernel.LocalFree(output_blob.pbData)
    if not isinstance(payload, dict) or payload.get("model") != MODEL or payload.get("stream") != STREAM:
        raise RuntimeError("本機配對憑證格式不正確。")
    if not re.fullmatch(r"[0-9a-f]{32}", str(payload.get("nonce", ""))):
        raise RuntimeError("本機配對憑證格式不正確。")
    if not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("fingerprint", ""))):
        raise RuntimeError("本機配對憑證格式不正確。")
    return payload


def _api(path: str, *, method: str = "GET", data: bytes | None = None, timeout: int = 6) -> object:
    headers = {"Accept": "application/json"}
    if data is not None and method == "POST":
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError("本機串流服務未回應。")
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise RuntimeError("本機串流回應過大。")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")[:256]


def _stream(credential: dict[str, str]) -> dict[str, object]:
    streams = _api("/api/streams")
    value = streams.get(STREAM) if isinstance(streams, dict) else None
    if not isinstance(value, dict):
        raise TypeError("找不到已配對的單一 C701 串流。")
    producers = value.get("producers")
    if not isinstance(producers, list) or len(producers) != 1:
        raise RuntimeError("C701 producer 並非唯一；未執行控制。")
    source = str(producers[0].get("url") or "") if isinstance(producers[0], dict) else ""
    parsed = urllib.parse.urlsplit(source)
    params = urllib.parse.parse_qs(parsed.query)
    if parsed.scheme != "xiaomi" or params.get("model") != [MODEL] or len(params.get("did", [])) != 1:
        raise RuntimeError("活動 producer 不是已配對的 C701。")
    expected = hashlib.sha256(f"{credential['nonce']}\0{params['did'][0]}\0{MODEL}".encode()).hexdigest()
    if not hmac.compare_digest(expected, credential["fingerprint"]):
        raise RuntimeError("活動 producer 與配對裝置不符。")
    return value


def _fresh_source(credential: dict[str, str]) -> str:
    accounts = _api("/api/xiaomi")
    if not isinstance(accounts, list) or len(accounts) != 1:
        raise RuntimeError("遠端小米帳號尚未就緒。")
    query = urllib.parse.urlencode({"id": str(accounts[0]), "region": "sg"})
    listing = _api("/api/xiaomi?" + query)
    sources = listing.get("sources", []) if isinstance(listing, dict) else []
    matches: list[str] = []
    for item in sources:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        parts = urllib.parse.urlsplit(url)
        params = urllib.parse.parse_qs(parts.query)
        if parts.scheme != "xiaomi" or params.get("model") != [MODEL] or len(params.get("did", [])) != 1:
            continue
        did = params["did"][0]
        expected = hashlib.sha256(f"{credential['nonce']}\0{did}\0{MODEL}".encode()).hexdigest()
        if hmac.compare_digest(expected, credential["fingerprint"]):
            matches.append(url)
    if len(matches) != 1:
        raise RuntimeError("新裝置清單無法唯一核對已配對攝影機。")
    return matches[0]


def _jpeg(ffmpeg: Path, rtsp_ip: str) -> bool:
    url = f"rtsp://{rtsp_ip}:28554/{STREAM}"
    try:
        result = subprocess.run(
            [str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "quiet", "-rtsp_transport", "tcp",
             "-i", url, "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=14, check=False, shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    frame = result.stdout
    return result.returncode == 0 and 4 <= len(frame) <= 2 * 1024 * 1024 and frame.startswith(b"\xff\xd8") and frame.endswith(b"\xff\xd9")


def _ptz_state() -> bool:
    try:
        value = _api("/api/xiaomi/ptz?" + urllib.parse.urlencode({"src": STREAM}), timeout=4)
    except (OSError, ValueError, RuntimeError, urllib.error.URLError):
        return False
    return isinstance(value, dict) and value.get("connected") is True


class Handler(BaseHTTPRequestHandler):
    server: ControlServer

    def log_message(self, _format: str, *_args: object) -> None:
        pass  # Never print a bearer token, Xiaomi URL, or request body.

    def _reply(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/v1/control" or self.headers.get("Content-Type") != "application/json":
            self._reply(404, {"success": False})
            return
        token = self.headers.get("Authorization", "")
        if not hmac.compare_digest(token, "Bearer " + self.server.credential["nonce"]):
            # Drain only a bounded request body before closing. On Windows an
            # unread POST body can reset TCP and discard the intended 403.
            try:
                rejected_length = int(self.headers.get("Content-Length", "0"))
                if 0 < rejected_length <= 1024:
                    self.rfile.read(rejected_length)
            except (OSError, ValueError):
                self.close_connection = True
            self._reply(403, {"success": False})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 1024:
                raise ValueError("body size")
            request = json.loads(self.rfile.read(length))
            if not isinstance(request, dict):
                raise TypeError("body type")
            result = self.server.dispatch(request)
            self._reply(200, result)
        except (OSError, TypeError, ValueError, RuntimeError, urllib.error.URLError):
            self._reply(200, {"success": False, "status": "failed", "message_zh_tw": "遠端控制失敗；未確認畫質或方向已改變。"})


class ControlServer(HTTPServer):
    def __init__(self, address: str, credential: dict[str, str], ffmpeg: Path, *, port: int = 28555) -> None:
        super().__init__((address, port), Handler)
        self.credential = credential
        self.ffmpeg = ffmpeg
        self.address = address

    def get_request(self) -> tuple[object, object]:
        connection, client_address = super().get_request()
        connection.settimeout(8)
        return connection, client_address

    def dispatch(self, request: dict[str, object]) -> dict[str, object]:
        action = request.get("action")
        if action == "status":
            _stream(self.credential)
            return {"success": True, "status": "available", "quality_available": True, "ptz_available": _ptz_state()}
        if action == "profile":
            profile = request.get("profile")
            if profile not in PROFILES:
                raise ValueError("profile")
            current = _stream(self.credential)
            source = _fresh_source(self.credential)
            original = str(current["producers"][0].get("url") or "")
            if not original.startswith("xiaomi://"):
                raise RuntimeError("非 Xiaomi producer")
            parsed = urllib.parse.urlsplit(source)
            query = [(key, value) for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                     if key != "subtype"]
            if not any(key == "audio" for key, _ in query):
                query.append(("audio", "0"))
            query.append(("subtype", str(profile).split("=", 1)[1]))
            url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path,
                                           urllib.parse.urlencode(query), parsed.fragment))
            params = urllib.parse.urlencode({"name": STREAM, "src": url})
            _api("/api/streams?" + params, method="PUT", data=b"", timeout=6)
            if _jpeg(self.ffmpeg, self.address):
                return {"success": True, "status": "verified", "profile": profile, "video_verified": True}
            # Restore the previous, already verified producer on failure.
            rollback = urllib.parse.urlencode({"name": STREAM, "src": original})
            _api("/api/streams?" + rollback, method="PUT", data=b"", timeout=6)
            return {"success": False, "status": "rolled_back", "video_verified": False}
        if action in {"ptz_state", "ptz_move", "ptz_stop"}:
            _stream(self.credential)
            if not _ptz_state():
                return {"success": False, "status": "ptz_unavailable", "ptz_available": False}
            if action == "ptz_state":
                return {"success": True, "status": "available", "ptz_available": True}
            payload: dict[str, str] = {"action": "stop" if action == "ptz_stop" else "move"}
            if action == "ptz_move":
                direction = request.get("direction")
                if direction not in {"left", "right", "up", "down"}:
                    raise ValueError("direction")
                payload["direction"] = str(direction)
            query = urllib.parse.urlencode({"src": STREAM})
            reply = _api("/api/xiaomi/ptz?" + query, method="POST", data=json.dumps(payload).encode(), timeout=5)
            if not isinstance(reply, dict) or reply.get("connected") is False:
                return {"success": False, "status": "ptz_failed", "ptz_available": True}
            return {"success": True, "status": "acknowledged", "ptz_available": True, "movement_verified": False}
        raise ValueError("action")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    args = parser.parse_args()
    state = args.state_dir.resolve()
    address = (state / "tailscale-ip.txt").read_text(encoding="ascii").strip()
    credential = _unseal(state / "control-credential.dpapi")
    if not args.ffmpeg.is_file() or ipaddress.ip_address(address) not in ipaddress.ip_network("100.64.0.0/10"):
        raise SystemExit("中繼控制前置檢查未通過。")
    server = ControlServer(address, credential, args.ffmpeg)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
