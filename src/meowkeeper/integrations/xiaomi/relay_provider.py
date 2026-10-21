"""Host-owned Tailnet relay. One fixed stream, one allowed peer, no cloud credentials."""
from __future__ import annotations

import hmac
import ipaddress
import json
import re
import secrets
import select
import socket
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .relay_control import load_control_nonce, save_control_nonce
from .relay_pairing import RelayPairing, device_fingerprint, encode_pairing_code

TAILNET = ipaddress.ip_network("100.64.0.0/10")


def tail_ip(value: object) -> str:
    try:
        address = ipaddress.ip_address(str(value))
    except ValueError:
        raise ValueError("請使用已登入 Tailscale 的電腦。") from None
    if address.version != 4 or address not in TAILNET:
        raise ValueError("請使用已登入 Tailscale 的電腦。")
    return str(address)


def valid_rtsp_request(packet: bytes, stream: str) -> bool:
    try:
        first, *headers = packet.decode("ascii").split("\r\n")
        method, target, protocol = first.split(" ")
        parsed = urlsplit(target)
        if method not in {"OPTIONS", "DESCRIBE", "SETUP", "PLAY", "TEARDOWN", "GET_PARAMETER"} or protocol != "RTSP/1.0":
            return False
        if target != "*" and (parsed.scheme not in {"", "rtsp"} or not re.fullmatch(r"/"+re.escape(stream)+r"(?:/trackID=\d{1,3})?",parsed.path)):
            return False
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            return False
        values = {line.split(":", 1)[0].lower(): line.split(":", 1)[1].strip() for line in headers if ":" in line}
        if int(values.get("content-length", "0")) != 0:
            return False
        transport=values.get("transport", "").upper()
        return method != "SETUP" or bool(re.fullmatch(r"RTP/AVP/TCP;UNICAST;INTERLEAVED=\d{1,3}-\d{1,3}",transport))
    except (ValueError, UnicodeError):
        return False


class StreamBridge(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, provider, address):
        self.provider = provider
        super().__init__(address, StreamHandler)

    def verify_request(self, request, client_address):
        return client_address[0] == self.provider.config["peer_ip"]


class StreamHandler(socketserver.BaseRequestHandler):
    def handle(self):
        provider = self.server.provider
        upstream = None
        try:
            self.request.settimeout(8)
            upstream = socket.create_connection(("127.0.0.1", provider.service.go2rtc.rtsp_port), timeout=5)
            provider.track(self.request, upstream)
            buffer = b""
            while provider.running:
                readable, _, _ = select.select([self.request, upstream], [], [], 15)
                if not readable:
                    continue
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    if source is upstream:
                        self.request.sendall(data)
                        continue
                    buffer += data
                    if len(buffer) > 131072:
                        return
                    while buffer:
                        if buffer.startswith(b"$"):
                            if len(buffer) < 4:
                                break
                            size = 4 + int.from_bytes(buffer[2:4], "big")
                            if len(buffer) < size:
                                break
                        else:
                            end = buffer.find(b"\r\n\r\n")
                            if end < 0:
                                if len(buffer) > 16384:
                                    return
                                break
                            size = end + 4
                            if not valid_rtsp_request(buffer[:size], provider.config["stream_name"]):
                                self.request.sendall(b"RTSP/1.0 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                                return
                        upstream.sendall(buffer[:size])
                        buffer = buffer[size:]
        except (OSError, ValueError):
            return
        finally:
            provider.untrack(self.request, upstream)
            if upstream:
                upstream.close()


class ControlHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        provider = self.server.provider
        code, result = 403, {"success": False, "message_zh_tw": "中繼控制未授權。"}
        try:
            if (self.client_address[0] == provider.config["peer_ip"] and self.path == "/v1/control"
                    and self.headers.get_content_type() == "application/json"
                    and hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + provider.nonce)):
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024:
                    raise ValueError
                result = provider.dispatch(json.loads(self.rfile.read(length)))
                code = 200
        except (OSError, RuntimeError, ValueError, TypeError):
            code, result = 200, {"success": False, "message_zh_tw": "中繼操作未完成；請查看攝影機所在地的診斷。"}
        body = json.dumps(result, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class RelayProvider:
    def __init__(self, service, *, network=None, firewall=None, seal=save_control_nonce, unseal=load_control_nonce):
        self.service = service
        self.network = network or dict
        self.firewall = firewall or (lambda *_args: False)
        self.seal, self.unseal = seal, unseal
        self.path: Path = service.state_path.with_name("xiaomi-relay-provider.json")
        self.secret_path = self.path.with_suffix(".dpapi")
        self.config: dict = {}
        self.nonce = ""
        self.running = False
        self.servers: list = []
        self.sockets: set = set()
        self.lock = threading.RLock()
        self.control_lock = threading.Lock()
        self.message = "尚未啟用此電腦的中繼。"
        self.busy = False
        self.starting = False
        self.generation = 0
        self.started_servers: list = []
        self.pending_code = ""
        self.pending_until = 0.0

    def status(self):
        with self.lock:
            return {"running": self.running, "busy": self.busy or self.starting, "device_id": self.config.get("device_id"),
                    "peer_ref": self.config.get("peer_ref"), "relay_only": self.config.get("relay_only"),
                    "message_zh_tw": self.message, "rtsp_port": 28554, "control_port": 28555}

    def track(self, *sockets):
        with self.lock:
            self.sockets.update(s for s in sockets if s is not None)

    def untrack(self, *sockets):
        with self.lock:
            self.sockets.difference_update(sockets)

    def enable(self, device_id, peer_ref, *, restore=False):
        with self.lock:
            if self.running or self.busy:
                raise ValueError("中繼正在運行或準備中；變更裝置前請先停止中繼。")
            self.busy = True
            generation = self.generation
        try:
            network = self.network()
            address = tail_ip(network.get("tailscale_ip"))
            peer = next((p for p in network.get("devices", []) if p.get("device_ref") == peer_ref
                         and not p.get("is_self") and p.get("online") is True), None)
            if not peer:
                raise ValueError("請選擇目前在線的 Tailscale 電腦。")
            peer_ip = next((ip for ip in peer.get("tailnet_ips", []) if ":" not in ip), None)
            peer_ip = tail_ip(peer_ip)
            if not self.firewall(peer_ip):
                raise ValueError("中繼防火牆尚未授權；請先完成下方的限縮規則授權。")
            resolved = self.service._resolve_device(device_id)
            if resolved is None:
                raise ValueError("請先登入並選擇攝影機。")
            internal, record = resolved
            if not record.get("device_id", record.get("did")) or record.get("region") not in {"cn","sg","de","ru","us","i2"}:
                raise ValueError("尚未確認此攝影機的裝置識別與區域，請更新攝影機清單。")
            active = next((d for d in self.service.devices() if d.get("selected")), None)
            if active and active["internal_id"] != internal:
                raise ValueError("本機正在監控另一台攝影機；請先選擇相同來源或停止監控。")
            if not self.service._camera_control_lock.acquire(blocking=False):
                raise ValueError("攝影機正在進行其他操作。")
            try:
                stream = self.service._ptz_stream(record)
                manager = self.service.source_manager
                if not stream:
                    remembered = self.service._remembered_preference(internal, record)
                    from .models import XiaomiQualityProfile
                    profile = XiaomiQualityProfile((remembered or {}).get("profile", "subtype=1"), None, None, None, None, None, False)
                    stream = (remembered or {}).get("stream_name") or record.get("stream_name") or "xiaomi_relay"
                    manager.activate_stream(internal, stream, profile)
                profile = manager.verify_active_profile(stream, (self.service.camera_preferences.get(internal) or {}).get("profile", "subtype=1"))
                if not profile.stable:
                    raise ValueError("未取得有效攝影機影格，沒有啟用中繼。")
                self.service._profiles[internal] = [profile]
                record["stream_name"] = stream
            finally:
                self.service._camera_control_lock.release()
            self.nonce = self.unseal(self.secret_path) if restore else secrets.token_hex(16)
            self.config = {"device_id": internal, "peer_ref": peer_ref, "peer_ip": peer_ip, "address": address,
                           "stream_name": stream, "relay_only": active is None, "enabled": True}
            with self.lock:
                if generation != self.generation:
                    raise ValueError("本次中繼準備已取消。")
                self._start_servers(address)
                if not restore:
                    self.seal(self.secret_path, self.nonce)
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(json.dumps(self.config), encoding="utf-8")
                temporary.replace(self.path)
                self.message = "中繼已啟用；只提供現有影音與控制。" if active is None else "已分享現有監控串流，未重複啟動 AI 或通知。"
                claim = RelayPairing(str(record["model"]), str(record["region"]), address, 28554, stream, self.nonce,
                                     device_fingerprint(self.nonce, str(record.get("device_id") or record.get("did")), str(record["model"])), int(time.time()) + 600)
                return {**self.status(), "success": True, "pairing_code": "" if restore else encode_pairing_code(claim),
                        "expires_in_seconds": 600}
        except (OSError, RuntimeError, ValueError):
            self.stop(revoke=False)
            raise
        finally:
            self.busy = False

    def _start_servers(self, address):
        self.servers.append(StreamBridge(self, (address, 28554)))
        control = ThreadingHTTPServer((address, 28555), ControlHandler)
        control.daemon_threads = True
        control.provider = self
        self.servers.append(control)
        self.running = True
        for server in self.servers:
            threading.Thread(target=server.serve_forever, daemon=True, name="xiaomi-relay").start()
            self.started_servers.append(server)

    def stop(self, *, revoke=True):
        with self.lock:
            self.generation += 1
            self.running = False
            sockets, self.sockets = self.sockets, set()
        for connection in sockets:
            try:
                connection.shutdown(socket.SHUT_RDWR)
                connection.close()
            except OSError:
                pass
        for server in self.servers:
            # shutdown only after serve_forever has actually started.
            if server in self.started_servers:
                server.shutdown()
            server.server_close()
        self.servers.clear()
        self.started_servers.clear()
        if revoke:
            self.secret_path.unlink(missing_ok=True)
            self.path.unlink(missing_ok=True)
            self.nonce = ""
            self.pending_code = ""
            self.pending_until = 0
        self.message = "中繼已停止；原本的本機監控設定保留。"
        return {**self.status(), "success": True}

    def restore(self):
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if saved.get("enabled") is True:
                self.enable(saved["device_id"], saved["peer_ref"], restore=True)
        except (OSError, ValueError, KeyError, RuntimeError):
            self.message = "已保存中繼設定；等待登入、Tailscale 與影格就緒後可重新啟用。"

    def dispatch(self, request):
        if not self.running or not isinstance(request, dict):
            raise ValueError("中繼尚未啟用。")
        action, stream = request.get("action"), self.config["stream_name"]
        client = self.service._get_ptz_client()
        if action == "status":
            return {"success": True, "quality_available": True, "ptz_available": bool(self.service.experimental_ptz_enabled)}
        if action != "profile" and not self.service.experimental_ptz_enabled:
            return {"success": False, "ptz_available": False, "message_zh_tw": "此中繼未啟用 PTZ。"}
        if action in {"ptz_state", "ptz_refresh"}:
            state = self.service._safe_ptz_payload(client.xiaomi_ptz_state(stream, refresh=action == "ptz_refresh"))
            position = state.get("position", {})
            valid = position.get("ret") == 0 and all(type(position.get(k)) is int and 0 <= position[k] <= 101 for k in ("angle", "elevation"))
            return {"success": state.get("connected") is True, "ptz_available": state.get("connected") is True,
                    "position_readback_verified": valid and action == "ptz_refresh", "state": state}
        if action == "ptz_stop":
            result = client.xiaomi_ptz_action(stream, "stop")
            confirmed = isinstance(result, dict) and result.get("connected") is True
            return {"success": confirmed, "status": "acknowledged" if confirmed else "unconfirmed", "ptz_available": True}
        if not self.control_lock.acquire(blocking=False):
            return {"success": False, "status": "busy"}
        try:
            if action in {"ptz_move", "ptz_position"}:
                if action == "ptz_position" and any(type(request.get(k)) is not int or not 1 <= request[k] <= 101 for k in ("angle", "elevation")):
                    raise ValueError("定位超出支援範圍。")
                if action == "ptz_move" and request.get("direction") not in {"left","right","up","down"}:
                    raise ValueError("不支援的方向。")
                state = self.dispatch({"action": "ptz_refresh"})
                if not state.get("ptz_available"):
                    raise ValueError
                if action == "ptz_position" and (not state.get("position_readback_verified") or not state["state"].get("capabilities", {}).get("absolute_move")):
                    raise ValueError
                try:
                    reply = client.xiaomi_ptz_action(stream, "position" if action == "ptz_position" else "move",
                        direction=request.get("direction"), angle=request.get("angle"), elevation=request.get("elevation"))
                    if action == "ptz_move":
                        time.sleep(.3)
                finally:
                    stopped = client.xiaomi_ptz_action(stream, "stop")
                confirmed = (isinstance(reply, dict) and reply.get("connected") is True
                             and isinstance(stopped, dict) and stopped.get("connected") is True)
                return {"success": confirmed, "status": "acknowledged" if confirmed else "unconfirmed", "ptz_available": True}
            if action == "profile":
                from .models import XiaomiQualityProfile
                from .quality_options import LIVE_PROFILE_OPTIONS
                name = request.get("profile")
                if name not in LIVE_PROFILE_OPTIONS:
                    raise ValueError
                internal = self.config["device_id"]
                previous = self.service.camera_preferences.get(internal) or {"profile": "subtype=1"}
                manager = self.service.source_manager
                try:
                    manager.activate_stream(internal, stream, XiaomiQualityProfile(name, None, None, None, None, None, False))
                    profile = manager.verify_active_profile(stream, name)
                    if not profile.stable:
                        raise ValueError
                except (OSError, RuntimeError, ValueError):
                    manager.activate_stream(internal, stream, XiaomiQualityProfile(previous["profile"], None, None, None, None, None, False))
                    return {"success": False, "status": "rolled_back"}
                self.service.camera_preferences.remember(internal, name, stream, metadata={
                    "width": profile.width, "height": profile.height, "fps": profile.fps,
                    "video_codec": profile.video_codec, "audio_codec": profile.audio_codec})
                self.service._profiles[internal] = [profile]
                return {"success": True, "status": "verified", "video_verified": True, "profile": name}
            raise ValueError("不支援的中繼操作。")
        finally:
            self.control_lock.release()
