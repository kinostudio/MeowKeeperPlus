"""Short-lived, secret-free pairing claims for a Xiaomi relay in the same Tailnet.

A claim is an invitation to test a stream, not proof that a video producer is
healthy.  The device fingerprint binds the invitation to the Xiaomi device
already present in the local account's device list without serializing its DID.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import re
import time
from dataclasses import dataclass
from typing import Any

PAIRING_PREFIX = "MKP1."
_STREAM_NAME = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_MODEL = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_TAILNET = ipaddress.ip_network("100.64.0.0/10")


def device_fingerprint(nonce: str, did: str, model: str) -> str:
    """The nonce makes the same device unlinkable across separate pairings."""

    return hashlib.sha256(f"{nonce}\0{did}\0{model}".encode()).hexdigest()


@dataclass(frozen=True)
class RelayPairing:
    model: str
    region: str
    tailnet_ip: str
    rtsp_port: int
    stream_name: str
    nonce: str
    fingerprint: str
    expires_at: int

    @property
    def rtsp_url(self) -> str:
        return f"rtsp://{self.tailnet_ip}:{self.rtsp_port}/{self.stream_name}"

    def matches_device(self, *, did: str, model: str) -> bool:
        return self.model == model and hmac.compare_digest(
            self.fingerprint, device_fingerprint(self.nonce, did, model),
        )


def decode_pairing_code(code: str, *, now: float | None = None) -> RelayPairing:
    """Reject malformed, expired, or non-Tailnet connection claims."""

    text = str(code).strip()
    if not text.startswith(PAIRING_PREFIX) or len(text) > 4096:
        raise ValueError("配對碼格式不正確。")
    encoded = text[len(PAIRING_PREFIX):]
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,4000}", encoded):
        raise ValueError("配對碼格式不正確。")
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        payload: Any = json.loads(raw)
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("配對碼無法讀取。") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("不支援的配對碼版本。")
    try:
        model = str(payload["model"])
        region = str(payload["region"])
        address = ipaddress.ip_address(str(payload["tailnet_ip"]))
        port = int(payload["rtsp_port"])
        stream = str(payload["stream_name"])
        nonce = str(payload["nonce"])
        fingerprint = str(payload["fingerprint"])
        expiry = int(payload["expires_at"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("配對碼欄位不完整。") from exc
    current = time.time() if now is None else float(now)
    if (
        not _MODEL.fullmatch(model)
        or region not in {"cn", "de", "i2", "ru", "sg", "us"}
        or address not in _TAILNET
        or not 1024 <= port <= 65535
        or not _STREAM_NAME.fullmatch(stream)
        or not re.fullmatch(r"[0-9a-f]{32}", nonce)
        or not _HEX.fullmatch(fingerprint)
        or not current < expiry <= current + 900
    ):
        raise ValueError("配對碼已過期，或中繼位址與裝置資料不符合安全限制。")
    return RelayPairing(model, region, str(address), port, stream, nonce, fingerprint, expiry)


def encode_pairing_code(pairing: RelayPairing) -> str:
    payload = {
        "version": 1,
        "model": pairing.model,
        "region": pairing.region,
        "tailnet_ip": pairing.tailnet_ip,
        "rtsp_port": pairing.rtsp_port,
        "stream_name": pairing.stream_name,
        "nonce": pairing.nonce,
        "fingerprint": pairing.fingerprint,
        "expires_at": pairing.expires_at,
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    return PAIRING_PREFIX + encoded


def validated_relay_url(*, address: str, port: int, stream: str) -> str:
    """Validate a saved relay endpoint before using it as an FFmpeg input."""

    try:
        ip = ipaddress.ip_address(address)
    except ValueError as exc:
        raise ValueError("遠端中繼位址不合法。") from exc
    if ip not in _TAILNET or not 1024 <= port <= 65535 or not _STREAM_NAME.fullmatch(stream):
        raise ValueError("遠端中繼位址不符合安全限制。")
    return f"rtsp://{ip}:{port}/{stream}"
