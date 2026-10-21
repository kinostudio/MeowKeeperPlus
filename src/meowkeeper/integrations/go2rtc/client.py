"""Small localhost-only go2rtc API client."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from .redaction import mask_response_body, redact_diagnostic_text


@dataclass(frozen=True)
class Go2RtcResponseDiagnostic:
    """Safe transport evidence from a localhost go2rtc request.

    The raw response is deliberately not retained.  ``body_masked`` is a
    bounded, secret-redacted summary suitable for the Xiaomi login report.
    """

    http_status: int | None
    content_type: str | None
    body_masked: str
    transport_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "http_status": self.http_status,
            "content_type": self.content_type,
            "body_masked": self.body_masked,
            "transport_error": self.transport_error,
        }


@dataclass(frozen=True)
class Go2RtcClient:
    base_url: str = "http://127.0.0.1:1984"
    timeout_seconds: float = 3.0

    def __post_init__(self) -> None:
        from urllib.parse import urlsplit

        parsed = urlsplit(self.base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("go2rtc client 只允許 localhost。")

    def get(self, path: str = "/api") -> Any:
        response = self._request(path, method="GET")
        if response["status"] is None or not 200 <= response["status"] < 300:
            raise RuntimeError("go2rtc localhost API 無法完成請求。")
        return response["data"]

    def request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        params: dict[str, str] | None = None,
        data: bytes | None = None,
        content_type: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any] | list[Any] | str:
        """Issue a bounded localhost request with an allow-listed query.

        The method deliberately returns only decoded response data.  It never
        logs or exposes the generated URL; this is important for Xiaomi
        ``xiaomi://`` source URLs which contain account/device references.
        """

        options = {"timeout_seconds": timeout_seconds} if timeout_seconds is not None else {}
        response = self._request(path, method=method, params=params, data=data, content_type=content_type, **options)
        status = response["status"]
        if status is None or not 200 <= status < 300:
            detail = redact_diagnostic_text(str(response.get("data") or ""), limit=240).strip()
            suffix = f"：{detail}" if detail else "。"
            raise RuntimeError(f"go2rtc localhost API 回應 HTTP {status or 'unknown'}{suffix}")
        return response["data"]

    def _request(
        self,
        path: str,
        *,
        method: str,
        params: dict[str, str] | None = None,
        data: bytes | None = None,
        content_type: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        self._validate_path(path)
        query = ""
        if params:
            query = "?" + urlencode({key: value for key, value in params.items() if value is not None})
        headers = {"Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(
            self.base_url.rstrip("/") + path + query,
            headers=headers,
            data=data,
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds or self.timeout_seconds) as response:
                raw = response.read(256 * 1024)
                status = getattr(response, "status", None) or response.getcode()
                content_type = response.headers.get("Content-Type")
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read(256 * 1024)
            except OSError:
                raw = b""
            return {
                "status": exc.code,
                "content_type": exc.headers.get("Content-Type") if exc.headers else None,
                "data": self._decode(raw),
            }
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("go2rtc localhost API 無法連線。") from exc
        return {"status": status, "content_type": content_type, "data": self._decode(raw)}

    @staticmethod
    def _decode(raw: bytes) -> Any:
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return raw.decode("utf-8", errors="replace")

    def get_diagnostic(self, path: str = "/api/xiaomi") -> Go2RtcResponseDiagnostic:
        """Capture status, content type and a masked body without auto-retry.

        This is intentionally a read-only transport helper.  It does not
        submit Xiaomi credentials and callers must decide whether an explicit
        user-requested login retry is allowed.
        """

        return self._request_diagnostic(path, method="GET")

    def post_xiaomi_login(
        self,
        *,
        username: str = "",
        password: str = "",
        captcha: str = "",
        verify: str = "",
    ) -> Go2RtcResponseDiagnostic:
        """Submit one explicit go2rtc Xiaomi form login request.

        Credentials and verification values are encoded in the request body
        only.  This method does not retry, persist, or log them.  Callers must
        perform a separate ``xiaomi_accounts()`` GET and classify that result
        with ``phase="get"`` after a successful HTTP 200 acknowledgement.
        """

        fields = {
            "username": username,
            "password": password,
            "captcha": captcha,
            "verify": verify,
        }
        if not any(str(value) for value in fields.values()):
            raise ValueError("小米登入 POST 至少需要一個官方登入欄位。")
        encoded = urlencode({key: value for key, value in fields.items() if value}).encode("utf-8")
        return self._request_diagnostic(
            "/api/xiaomi",
            method="POST",
            data=encoded,
            content_type="application/x-www-form-urlencoded",
        )

    def _request_diagnostic(
        self,
        path: str,
        *,
        method: str,
        data: bytes | None = None,
        content_type: str | None = None,
    ) -> Go2RtcResponseDiagnostic:
        self._validate_path(path)
        headers = {"Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(
            self.base_url.rstrip("/") + path,
            headers=headers,
            data=data,
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read(64 * 1024)
                return Go2RtcResponseDiagnostic(
                    http_status=getattr(response, "status", None) or response.getcode(),
                    content_type=response.headers.get("Content-Type"),
                    body_masked=mask_response_body(raw),
                )
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read(64 * 1024)
            except OSError:
                raw = b""
            return Go2RtcResponseDiagnostic(
                http_status=exc.code,
                content_type=exc.headers.get("Content-Type") if exc.headers else None,
                body_masked=mask_response_body(raw),
                transport_error=None,
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return Go2RtcResponseDiagnostic(
                http_status=None,
                content_type=None,
                body_masked="",
                transport_error=redact_diagnostic_text(f"{type(exc).__name__}: {exc}"),
            )

    @staticmethod
    def _validate_path(path: str) -> None:
        if not path.startswith("/") or ".." in path or "?" in path or "#" in path:
            raise ValueError("go2rtc API path 不安全。")

    def api(self) -> Any:
        return self.get("/api")

    def streams(self) -> Any:
        return self.get("/api/streams")

    def xiaomi_accounts(self) -> list[str]:
        payload = self.request_json("/api/xiaomi")
        if not isinstance(payload, list):
            return []
        return [str(item) for item in payload if str(item).strip()]

    def xiaomi_sources(self, account_id: str, *, region: str = "cn") -> list[dict[str, Any]]:
        if not account_id or any(char in account_id for char in "\r\n"):
            raise ValueError("小米帳號參考值不正確。")
        payload = self.request_json("/api/xiaomi", params={"id": account_id, "region": region})
        if not isinstance(payload, dict) or not isinstance(payload.get("sources"), list):
            return []
        return [item for item in payload["sources"] if isinstance(item, dict) and item.get("url")]

    def put_stream(self, name: str, source: str) -> dict[str, Any] | str:
        if not name or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in name):
            raise ValueError("go2rtc stream 名稱不安全。")
        if not source.startswith(("xiaomi://", "rtsp://", "http://", "https://")):
            raise ValueError("go2rtc source scheme 不受支援。")
        # Avoid replacing an active stream for an identical registration.
        # Raw source values remain in memory only and are never logged.
        existing = self.request_json("/api/streams")
        item = existing.get(name) if isinstance(existing, dict) else None
        producers = item.get("producers", []) if isinstance(item, dict) else []
        if len(producers) == 1 and isinstance(producers[0], dict) and producers[0].get("url") == source:
            return {"status": "unchanged"}
        return self.request_json("/api/streams", method="PUT", params={"name": name, "src": source})

    def delete_stream(self, name: str) -> dict[str, Any] | str:
        if not name or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in name):
            raise ValueError("go2rtc stream 名稱不安全。")
        return self.request_json("/api/streams", method="DELETE", params={"src": name})

    def stream_status(self, name: str) -> dict[str, Any] | str:
        if not name or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in name):
            raise ValueError("go2rtc stream 名稱不安全。")
        return self.request_json("/api/streams", params={"src": name})

    @staticmethod
    def _validate_stream_name(name: str) -> None:
        if not name or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in name):
            raise ValueError("go2rtc stream 名稱不安全。")

    def xiaomi_ptz_state(self, stream_name: str, *, refresh: bool = False) -> dict[str, Any] | str:
        """Read the optional Xiaomi MISS PTZ state from a local go2rtc fork.

        The endpoint is deliberately opt-in at the service layer.  This
        client only accepts an opaque local stream name and never receives a
        Xiaomi source URL, account identifier or token.
        """

        self._validate_stream_name(stream_name)
        params = {"src": stream_name}
        if refresh:
            params["refresh"] = "true"
        if refresh:
            # The server's motor response timeout is five seconds.
            return self.request_json("/api/xiaomi/ptz", params=params, timeout_seconds=8.0)
        return self.request_json("/api/xiaomi/ptz", params=params)

    def xiaomi_ptz_action(
        self,
        stream_name: str,
        action: str,
        *,
        direction: str | None = None,
        angle: int | None = None,
        elevation: int | None = None,
    ) -> dict[str, Any] | list[Any] | str:
        """Send one allow-listed Xiaomi MISS PTZ action.

        The fork's native endpoint supports relative movement, stop,
        calibration, refresh and absolute position.  Values are validated
        before the request so a malformed Dashboard payload cannot reach the
        camera.  No action is retried automatically.
        """

        self._validate_stream_name(stream_name)
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in {"move", "stop", "calibrate", "refresh", "position"}:
            raise ValueError("不支援的 Xiaomi PTZ 操作。")
        body: dict[str, Any] = {"action": normalized_action}
        if normalized_action == "move":
            normalized_direction = str(direction or "").strip().lower()
            if normalized_direction not in {"left", "right", "up", "down"}:
                raise ValueError("Xiaomi PTZ 方向不正確。")
            body["direction"] = normalized_direction
        elif normalized_action == "position":
            if isinstance(angle, bool) or isinstance(elevation, bool) or not isinstance(angle, int) or not isinstance(elevation, int):
                raise ValueError("Xiaomi PTZ 位置必須是整數。")
            if not 1 <= angle <= 101 or not 1 <= elevation <= 101:
                raise ValueError("Xiaomi PTZ 位置必須介於 1 到 101。")
            body["angle"] = angle
            body["elevation"] = elevation
        encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self.request_json(
            "/api/xiaomi/ptz",
            method="POST",
            params={"src": stream_name},
            data=encoded,
            content_type="application/json",
            # RefreshPosition holds the producer's motor mutex for up to
            # five seconds. The default three-second HTTP deadline can expire
            # while an otherwise valid position command waits for that mutex.
            timeout_seconds=8.0,
        )

    def probe_stream(self, name: str) -> dict[str, Any] | str:
        """Ask go2rtc to open all available media tracks for a stream."""

        if not name or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in name):
            raise ValueError("go2rtc stream 名稱不安全。")
        return self.request_json(
            "/api/streams",
            params={"src": name, "video": "all", "audio": "all", "microphone": ""},
        )

    def has_jpeg_frame(self, name: str, *, timeout_seconds: float = 12.0) -> bool:
        """Verify one real frame from a temporary localhost-only stream."""

        try:
            self.get_frame_jpeg(name, timeout_seconds=timeout_seconds)
        except (RuntimeError, ValueError):
            return False
        return True

    def get_frame_jpeg(self, name: str, *, timeout_seconds: float = 12.0) -> bytes:
        """Read one real JPEG from the existing localhost go2rtc producer.

        This endpoint is intentionally restricted to an opaque stream name.
        It does not accept a Xiaomi source URL and therefore cannot create a
        second camera connection. The bytes are returned only after a strict
        JPEG marker check and a bounded read.
        """

        self._validate_stream_name(name)
        if not 0 < timeout_seconds <= 30:
            raise ValueError("影格測試逾時設定不正確。")
        request = urllib.request.Request(
            self.base_url.rstrip("/") + "/api/frame.jpeg?" + urlencode({"src": name}),
            headers={"Accept": "image/jpeg"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                if (getattr(response, "status", None) or response.getcode()) != 200:
                    raise RuntimeError("go2rtc frame API 回應非成功狀態。")
                jpeg = response.read(2 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"go2rtc frame API 回應 HTTP {exc.code}。") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("go2rtc frame API 無法連線。") from exc
        if len(jpeg) > 2 * 1024 * 1024:
            raise RuntimeError("go2rtc JPEG 超過安全大小上限。")
        if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
            raise RuntimeError("go2rtc 回應不是完整 JPEG。")
        return jpeg
