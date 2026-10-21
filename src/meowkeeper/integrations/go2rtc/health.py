"""Bounded localhost health checks."""

from __future__ import annotations

import socket
import urllib.error
import urllib.request


def http_health(base_url: str, *, timeout_seconds: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/api", timeout=timeout_seconds) as response:
            return 200 <= response.status < 500
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def port_health(host: str, port: int, *, timeout_seconds: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False
