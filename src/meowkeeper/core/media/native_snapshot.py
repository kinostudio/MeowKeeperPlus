"""從已完成的壓縮環形分段擷取原始解析度；不再連線攝影機。"""
from __future__ import annotations

import subprocess

from meowkeeper.runtime import bundled_binary


def snapshot_from_ring(ring, *, timeout: float = 8.0) -> bytes | None:
    if ring is None:
        return None
    records = ring.index.all()
    if not records:
        return None
    acquired = ring.index.acquire(records[-1:])
    if not acquired:
        return None
    try:
        result = subprocess.run([
            bundled_binary("ffmpeg"), "-hide_banner", "-loglevel", "error",
            "-i", str(acquired[0].path), "-map", "0:v:0", "-an", "-frames:v", "1",
            "-q:v", "2", "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1",
        ], capture_output=True, timeout=timeout, shell=False, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        payload = result.stdout
        if result.returncode == 0 and len(payload) <= 32 * 1024**2 and payload.startswith(b"\xff\xd8") and payload.endswith(b"\xff\xd9"):
            return payload
    except (OSError, subprocess.TimeoutExpired):
        pass
    finally:
        ring.index.release(acquired)
    return None
