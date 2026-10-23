"""stdlib-only helper；不得輸出 traceback、原始 HTTP 錯誤內容或秘密。"""

import http.client
import ipaddress
import json
import math
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPHandler,
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

dispatched = False


class DispatchObserver:
    def request(self, *args, **kwargs):
        global dispatched
        super().request(*args, **kwargs)
        dispatched = True
        sys.stdout.buffer.write(b'{"event":"request_dispatched"}\n')
        sys.stdout.buffer.flush()


class ObservedHTTP(DispatchObserver, http.client.HTTPConnection):
    pass


class ObservedHTTPS(DispatchObserver, http.client.HTTPSConnection):
    pass


class ObservedHTTPHandler(HTTPHandler):
    def http_open(self, req):
        return self.do_open(ObservedHTTP, req)


class ObservedHTTPSHandler(HTTPSHandler):
    def https_open(self, req):
        kwargs = {"context": self._context}
        if hasattr(self, "_check_hostname"):
            kwargs["check_hostname"] = self._check_hostname
        return self.do_open(ObservedHTTPS, req, **kwargs)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    result = {"error": "unavailable"}
    request_started, observed_status, response_elapsed = False, None, None
    http_timings = {}
    try:
        payload = json.loads(sys.stdin.buffer.read(8 * 1024 * 1024 + 1))
        endpoint = payload["endpoint"]
        parsed = urlsplit(endpoint)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname:
            raise ValueError
        local = False
        try:
            local = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            pass
        if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
            raise ValueError
        method = payload.get("method", "POST")
        if method not in {"POST", "GET"}:
            raise ValueError
        request = Request(endpoint, json.dumps(payload["body"]).encode("utf-8") if method == "POST" else None,
                          headers=payload["headers"], method=method)
        opener = build_opener(ProxyHandler({}), NoRedirect(), ObservedHTTPHandler(), ObservedHTTPSHandler())
        request_started = True
        started = time.monotonic()
        http_timings["http_started_at"] = datetime.now(timezone.utc).isoformat()
        with opener.open(request, timeout=float(payload["timeout"])) as response:
            observed_status = response.status
            raw = response.read(1024 * 1024 + 1)
            response_elapsed = (time.monotonic() - started) * 1000
            http_timings["response_received_at"] = datetime.now(timezone.utc).isoformat()
            if len(raw) > 1024 * 1024:
                raise TypeError
            decoded = json.loads(raw)
            if not isinstance(decoded, dict):
                raise TypeError
            result = {"http_status": response.status, "response": decoded}
    except HTTPError as error:
        response_elapsed = (time.monotonic() - started) * 1000
        http_timings["response_received_at"] = datetime.now(timezone.utc).isoformat()
        category = ("auth_error" if error.code in {401, 403} else
                    "rate_limited" if error.code in {402, 429} else
                    "unavailable" if error.code >= 500 else "bad_response")
        result = {"error": category, "http_status": error.code}
        if error.code == 429:
            header = error.headers.get("Retry-After", "")
            try:
                try:
                    seconds = float(header)
                except ValueError:
                    date = parsedate_to_datetime(header)
                    if date.tzinfo is None:
                        date = date.replace(tzinfo=timezone.utc)
                    seconds = date.timestamp() - time.time()
                if math.isfinite(seconds):
                    result["retry_after_seconds"] = min(86400, max(0, seconds))
            except (ValueError, TypeError, OverflowError):
                pass
        error.close()
    except TimeoutError:
        result = {"error": "timeout"}
    except URLError:
        result = {"error": "unavailable"}
    except Exception:  # noqa: BLE001 - 外部資料不得透過 traceback 洩漏
        result = {"error": "bad_response"}
    if observed_status is not None:
        result.setdefault("http_status", observed_status)
    result["request_dispatched"] = True if dispatched else None if request_started else False
    if response_elapsed is not None:
        result["api_elapsed_ms"] = response_elapsed
    result["http_timings"] = http_timings
    sys.stdout.buffer.write(json.dumps(result).encode("utf-8") + b'\n')


if __name__ == "__main__":
    main()
