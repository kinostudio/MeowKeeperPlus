"""小米裝置探索的可測試契約，不自行登入或猜測支援機型。"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

from meowkeeper.integrations.go2rtc.client import Go2RtcClient
from meowkeeper.runtime import bundled_binary

from .models import XiaomiDeviceIdentity
from .security import public_device_payload


def list_confirmed_devices(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items:
        if not item.get("model"):
            continue
        result.append(public_device_payload(dict(item)))
    return result


def require_user_confirmation(identity: XiaomiDeviceIdentity) -> None:
    if not identity.confirmed_by_user:
        raise ValueError("請先確認機身標籤、區域與韌體版本；不能僅依名稱宣稱支援。")


def _stable_internal_id(source: str, name: str) -> str:
    digest = hashlib.sha256(f"{name}\x00{source}".encode("utf-8", errors="replace")).hexdigest()[:10]
    return f"xiaomi_camera_{digest}"


def _masked_ip(info: object) -> str | None:
    match = re.search(r"(?<!\d)(\d{1,3}(?:\.\d{1,3}){3})(?!\d)", str(info or ""))
    if not match:
        return None
    parts = match.group(1).split(".")
    return ".".join(parts[:3] + ["xxx"])


class Go2RtcXiaomiDiscovery:
    """Read the real, already authenticated go2rtc Xiaomi sources.

    The full ``xiaomi://`` source is kept only in this process so a later
    controlled stream PUT can use it.  ``discover`` returns an opaque internal
    reference plus presentation-safe metadata; callers must not persist the
    private ``source_url`` field.
    """

    REGIONS = ("cn", "de", "i2", "ru", "sg", "us")

    def __init__(
        self,
        client: Go2RtcClient,
        *,
        region: str = "cn",
        rtsp_port: int = 8554,
        region_cache_path: str | Path | None = None,
        region_cache_fallback_path: str | Path | None = None,
        fallback_region: str | None = None,
    ) -> None:
        if not 1 <= int(rtsp_port) <= 65535:
            raise ValueError("go2rtc RTSP port 必須介於 1 到 65535。")
        if region != "auto" and region not in self.REGIONS:
            raise ValueError("不支援的 Xiaomi 後端 region。")
        if fallback_region is not None and fallback_region not in self.REGIONS:
            raise ValueError("不支援的 Xiaomi 候選 region。")
        self.client = client
        self.region = region
        self.fallback_region = fallback_region
        self.rtsp_port = int(rtsp_port)
        self.region_cache_path = Path(region_cache_path) if region_cache_path else None
        self.region_cache_fallback_path = Path(region_cache_fallback_path) if region_cache_fallback_path else None
        self.account_id: str | None = None
        self._sources: dict[str, str] = {}

    def discover(self) -> list[dict[str, Any]]:
        accounts = self.client.xiaomi_accounts()
        if not accounts:
            self.account_id = None
            self._sources.clear()
            return []
        # The go2rtc WebUI selects an account and then queries the region.  A
        # single configured account is the supported MVP shape; if more are
        # present, use the first rather than leaking account identifiers.
        self.account_id = accounts[0]
        if self.region == "auto":
            return self._discover_auto()
        sources = self.client.xiaomi_sources(self.account_id, region=self.region)
        self._sources.clear()
        result: list[dict[str, Any]] = []
        for source in sources:
            source_url = str(source.get("url") or "")
            if not source_url.startswith("xiaomi://"):
                continue
            name = str(source.get("name") or "小米攝影機").strip()[:120]
            info = str(source.get("info") or "")[:240]
            query = parse_qs(urlsplit(source_url).query)
            model = str((query.get("model") or [""])[0] or "未知型號")[:120]
            internal_id = _stable_internal_id(source_url, name)
            self._sources[internal_id] = source_url
            result.append(
                {
                    "internal_id": internal_id,
                    "device_id": str((query.get("did") or [""])[0]),
                    "display_name": name,
                    "model": model,
                    "region": self.region,
                    "lan_ip": _masked_ip(info),
                    "online_status": "available",
                    "supported_status": "candidate",
                    "available_profiles": ["subtype=auto", "subtype=sd", "subtype=hd", *[f"subtype={i}" for i in range(6)]],
                    "source_reference_present": True,
                    # Private process-only value; XiaomiSetupService removes it
                    # before writing state or returning the API response.
                    "source_url": source_url,
                }
            )
        return result

    @staticmethod
    def _region_key(account: str, did: str, model: str) -> str:
        # Neither the account nor DID may be persisted in plain text.
        return hashlib.sha256(f"{account}\x00{did}\x00{model}".encode()).hexdigest()

    def _load_region_cache(self) -> dict[str, str]:
        for path in (self.region_cache_path, self.region_cache_fallback_path):
            if path is None or not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            regions = payload.get("regions") if isinstance(payload, dict) else None
            if isinstance(regions, dict):
                return {
                    key: value for key, value in regions.items()
                    if isinstance(key, str) and re.fullmatch(r"[0-9a-f]{64}", key)
                    and value in self.REGIONS
                }
        return {}

    def _save_region_cache(self, regions: Mapping[str, str]) -> None:
        for path in (self.region_cache_path, self.region_cache_fallback_path):
            if path is None:
                continue
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".tmp")
                temporary.write_text(json.dumps({"version": 1, "regions": dict(regions)}, indent=2) + "\n", encoding="utf-8")
                os.replace(temporary, path)
                return
            except OSError:
                # A denied AppData write must not prevent a verified stream.
                continue

    def _probe_region_frame(self, source: str, key: str, region: str) -> bool:
        """One temporary SD stream; consumers run sequentially, never a sweep."""

        stream_name = "xiaomi_region_" + hashlib.sha256(f"{key}:{region}".encode()).hexdigest()[:12]
        try:
            self.client.put_stream(stream_name, self._with_profile(source, "subtype=sd"))
            if self.client.has_jpeg_frame(stream_name, timeout_seconds=12.0):
                return True
            # go2rtc's JPEG endpoint may depend on ffmpeg in the SYSTEM PATH.
            # The portable build carries its own FFmpeg; use it only after the
            # first consumer has finished, against the same localhost restream.
            return isinstance(self.client, Go2RtcClient) and self._probe_bundled_jpeg_frame(stream_name)
        except (OSError, RuntimeError, ValueError):
            return False
        finally:
            try:
                self.client.delete_stream(stream_name)
            except (OSError, RuntimeError, ValueError):
                pass

    def _probe_bundled_jpeg_frame(self, stream_name: str) -> bool:
        return self._read_bundled_jpeg_frame(stream_name) is not None

    def _read_bundled_jpeg_frame(self, stream_name: str) -> bytes | None:
        ffmpeg = bundled_binary("ffmpeg")
        if not ffmpeg:
            return None
        local_url = f"rtsp://127.0.0.1:{self.rtsp_port}/{stream_name}"
        command = [
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
            "-rtsp_transport", "tcp", "-i", local_url,
            "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, timeout=20, check=False, shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        jpeg = completed.stdout
        if (
            completed.returncode != 0
            or not 4 <= len(jpeg) <= 2 * 1024 * 1024
            or not jpeg.startswith(b"\xff\xd8")
            or not jpeg.endswith(b"\xff\xd9")
        ):
            return None
        return jpeg

    @staticmethod
    def _jpeg_dimensions(jpeg: bytes) -> tuple[int, int] | None:
        """Read JPEG dimensions without making Pillow a discovery dependency."""

        if not jpeg.startswith(b"\xff\xd8"):
            return None
        index = 2
        sof_markers = {
            *range(0xC0, 0xC4),
            *range(0xC5, 0xC8),
            *range(0xC9, 0xCC),
            *range(0xCD, 0xD0),
        }
        while index + 3 < len(jpeg):
            if jpeg[index] != 0xFF:
                index += 1
                continue
            while index < len(jpeg) and jpeg[index] == 0xFF:
                index += 1
            if index >= len(jpeg):
                break
            marker = jpeg[index]
            index += 1
            if marker in {0xD8, 0xD9}:
                continue
            if marker == 0xDA or index + 2 > len(jpeg):
                break
            segment_length = int.from_bytes(jpeg[index:index + 2], "big")
            if segment_length < 2 or index + segment_length > len(jpeg):
                break
            if marker in sof_markers and index + 7 <= len(jpeg):
                height = int.from_bytes(jpeg[index + 3:index + 5], "big")
                width = int.from_bytes(jpeg[index + 5:index + 7], "big")
                if width > 0 and height > 0:
                    return width, height
            index += segment_length
        return None

    def _discover_auto(self) -> list[dict[str, Any]]:
        assert self.account_id is not None
        candidates: dict[str, dict[str, dict[str, str]]] = {}
        for region in self.REGIONS:
            try:
                sources = self.client.xiaomi_sources(self.account_id, region=region)
            except (OSError, RuntimeError, ValueError):
                continue
            for item in sources:
                source = str(item.get("url") or "")
                if not source.startswith("xiaomi://"):
                    continue
                query = parse_qs(urlsplit(source).query)
                did = str((query.get("did") or [""])[0])
                model = str((query.get("model") or [""])[0])
                if not did or not model:
                    continue
                key = self._region_key(self.account_id, did, model)
                candidates.setdefault(key, {})[region] = {
                    "source": source,
                    "did": did,
                    "model": model,
                    "name": str(item.get("name") or "小米攝影機")[:120],
                    "info": str(item.get("info") or "")[:240],
                }

        cached = self._load_region_cache()
        changed = False
        self._sources.clear()
        result: list[dict[str, Any]] = []
        for key, by_region in candidates.items():
            preferred = cached.get(key)
            fallback = self.fallback_region if self.fallback_region in by_region else None
            ordered = ([preferred] if preferred in by_region else []) + [
                region for region in ([fallback] if fallback else []) + list(self.REGIONS)
                if region in by_region and region != preferred
            ]
            ordered = list(dict.fromkeys(ordered))
            # A cached region was already proven by a real frame. Reuse it to
            # avoid opening a second producer during later Dashboard refreshes.
            selected = preferred if preferred in by_region else None
            verified_now = False
            if selected is None:
                for region in ordered:
                    if self._probe_region_frame(by_region[region]["source"], key, region):
                        selected = region
                        verified_now = True
                        cached[key] = region
                        changed = True
                        break
            candidate = fallback if selected is None else None
            chosen = selected or candidate
            item = by_region[chosen] if chosen else by_region[ordered[0]]
            internal_id = f"xiaomi_camera_{key[:10]}"
            if chosen:
                self._sources[internal_id] = item["source"]
            result.append({
                "internal_id": internal_id,
                "device_id": item["did"],
                "display_name": item["name"],
                "model": item["model"],
                "region": chosen or "unconfirmed",
                "lan_ip": _masked_ip(item["info"]),
                "online_status": "verified_frame" if verified_now else ("previously_verified" if selected else ("region_candidate" if candidate else "unverified")),
                "supported_status": "candidate" if chosen else "blocked",
                "available_profiles": ["subtype=sd", "subtype=auto", "subtype=hd"],
                "source_reference_present": bool(chosen),
                **({"source_url": item["source"]} if chosen else {}),
            })
        if changed:
            self._save_region_cache(cached)
        return result

    def source_for(self, internal_id: str) -> str | None:
        return self._sources.get(internal_id)

    @staticmethod
    def _with_profile(source: str, profile_name: str) -> str:
        parsed = urlsplit(source)
        query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != "subtype"]
        # Video & audio monitoring: default to audio=1 so live sound and event clips have audio
        # Preserve an explicit audio choice supplied by the source.
        if not any(key == "audio" for key, _ in query):
            query.append(("audio", "1"))
        if profile_name.startswith("subtype="):
            query.append(("subtype", profile_name.split("=", 1)[1]))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))

    @staticmethod
    def _parse_rate(value: object) -> float | None:
        text = str(value or "").strip()
        if not text or text in {"0/0", "N/A"}:
            return None
        try:
            if "/" in text:
                numerator, denominator = text.split("/", 1)
                return float(numerator) / float(denominator) if float(denominator) else None
            return float(text)
        except (TypeError, ValueError, ZeroDivisionError):
            return None

    def profile_probe(
        self,
        device: Mapping[str, Any],
        profile_names: tuple[str, ...],
        *,
        timeout_seconds: float = 15.0,
    ) -> list[Any]:
        """Create one temporary local go2rtc stream per profile and FFprobe it."""

        from .models import XiaomiQualityProfile

        source = str(device.get("source_url") or "")
        internal_id = str(device.get("internal_id") or "unknown")
        ffprobe = bundled_binary("ffprobe")
        if not source or not ffprobe:
            return [XiaomiQualityProfile(name, None, None, None, None, None, False, error="FFprobe 不可用。") for name in profile_names]
        results: list[XiaomiQualityProfile] = []
        for index, name in enumerate(profile_names):
            digest = hashlib.sha256(f"{internal_id}:{name}:{index}".encode()).hexdigest()[:12]
            stream_name = f"xiaomi_probe_{digest}"
            video: dict[str, Any] | None = None
            audio_codec: str | None = None
            error: str | None = None
            put_p2pid_error = False
            stop_profile_variants = False
            try:
                self.client.put_stream(stream_name, self._with_profile(source, name))
                # FFprobe is the single bounded consumer and the authoritative
                # check. The extra go2rtc /api/streams?video=all request can
                # time out during Xiaomi cold start before FFprobe gets a chance.
                # Preserve the cheap preflight for unverified/legacy devices so
                # a profile sweep cannot run many long FFprobe attempts.
                if device.get("online_status") not in {"verified_frame", "previously_verified", "region_candidate"}:
                    try:
                        self.client.probe_stream(stream_name)
                    except RuntimeError as exc:
                        error = str(exc)[:240]
                    if error:
                        results.append(
                            XiaomiQualityProfile(name, None, None, None, None, None, False, error=error)
                        )
                        if "p2pid" in error.lower():
                            for remaining in profile_names[index + 1 :]:
                                results.append(XiaomiQualityProfile(
                                    remaining, None, None, None, None, None, False,
                                    error="略過：來源尚未分配 p2pid。",
                                ))
                            break
                        continue
                local_url = f"rtsp://127.0.0.1:{self.rtsp_port}/{stream_name}"
                command = [
                    ffprobe,
                    "-v",
                    "error",
                    "-rtsp_transport",
                    "tcp",
                    "-timeout",
                    str(int(timeout_seconds * 1_000_000)),
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height,codec_name,avg_frame_rate,r_frame_rate",
                    "-of",
                    "json",
                    local_url,
                ]
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_seconds,
                    check=False,
                    shell=False,
                )
                if completed.returncode != 0:
                    error = "FFprobe 無法讀取此 profile。"
                else:
                    payload = json.loads(completed.stdout or "{}")
                    streams = payload.get("streams") if isinstance(payload, dict) else None
                    video = streams[0] if isinstance(streams, list) and streams and isinstance(streams[0], dict) else None
                    if video is None:
                        error = "未取得影像串流。"
                # HLC7 may deliver valid HEVC packets while FFprobe cannot
                # decode its first reference frame.  Use the same temporary
                # localhost stream with the bundled FFmpeg fallback, in
                # sequence after FFprobe, rather than declaring the profile
                # unusable.  This does not add a camera connection.
                if video is None:
                    fallback_jpeg = self._read_bundled_jpeg_frame(stream_name)
                    dimensions = self._jpeg_dimensions(fallback_jpeg or b"")
                    if dimensions is not None:
                        video = {
                            "width": dimensions[0],
                            "height": dimensions[1],
                            "codec_name": None,
                            "avg_frame_rate": None,
                        }
                        error = None
                # Audio is optional.  A failed audio probe never invalidates a
                # video-only Xiaomi source.
                if video is not None:
                    audio_command = [*command[: command.index("-select_streams")], "-select_streams", "a:0", "-show_entries", "stream=codec_name", "-of", "json", local_url]
                    audio = subprocess.run(
                        audio_command,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=timeout_seconds,
                        check=False,
                        shell=False,
                    )
                    if audio.returncode == 0:
                        audio_payload = json.loads(audio.stdout or "{}")
                        audio_streams = audio_payload.get("streams") if isinstance(audio_payload, dict) else None
                        if isinstance(audio_streams, list) and audio_streams and isinstance(audio_streams[0], dict):
                            audio_codec = str(audio_streams[0].get("codec_name") or "") or None
            except RuntimeError as exc:
                # The localhost API error can contain a config path, source
                # URL, or other implementation detail.  Keep only a safe
                # user-facing classification and stop a p2pid failure from
                # turning the Dashboard action into an unhandled HTTP 500.
                lowered = str(exc).lower()
                if "p2pid" in lowered:
                    error = "go2rtc 尚未取得攝影機 p2pid。"
                    put_p2pid_error = True
                elif "access is denied" in lowered or "permission denied" in lowered:
                    error = "go2rtc 暫時串流設定無法寫入；請確認其工作目錄可寫入。"
                    stop_profile_variants = True
                else:
                    error = "go2rtc 無法建立暫時串流。"
            except (OSError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError):
                error = "FFprobe 執行失敗或逾時。"
            finally:
                try:
                    self.client.delete_stream(stream_name)
                except (RuntimeError, ValueError):
                    pass
            if put_p2pid_error or stop_profile_variants:
                results.append(
                    XiaomiQualityProfile(
                        name=name,
                        width=None,
                        height=None,
                        fps=None,
                        video_codec=None,
                        audio_codec=None,
                        stable=False,
                        error=error,
                    )
                )
                for remaining in profile_names[index + 1 :]:
                    results.append(
                        XiaomiQualityProfile(
                            name=remaining,
                            width=None,
                            height=None,
                            fps=None,
                            video_codec=None,
                            audio_codec=None,
                            stable=False,
                            error=(
                                "略過：來源尚未分配 p2pid。"
                                if put_p2pid_error
                                else "略過：go2rtc 暫時串流建立失敗。"
                            ),
                        )
                    )
                break
            results.append(
                XiaomiQualityProfile(
                    name=name,
                    width=int(video.get("width")) if video and str(video.get("width", "")).isdigit() else None,
                    height=int(video.get("height")) if video and str(video.get("height", "")).isdigit() else None,
                    fps=self._parse_rate(video.get("avg_frame_rate")) if video else None,
                    video_codec=str(video.get("codec_name") or "") or None if video else None,
                    audio_codec=audio_codec,
                    stable=bool(video and not error),
                    duration_seconds=timeout_seconds if video and not error else None,
                    error=error,
                )
            )
        return results

    def activate_stream(self, internal_id: str, stream_name: str, profile: Any) -> str:
        source = self.source_for(internal_id)
        if not source:
            raise RuntimeError("找不到 Xiaomi source reference；請重新載入裝置。")
        profile_name = str(getattr(profile, "name", "subtype=auto"))
        self.client.put_stream(stream_name, self._with_profile(source, profile_name))
        return f"rtsp://127.0.0.1:{self.rtsp_port}/{stream_name}"

    def verify_active_profile(self, stream_name: str, profile_name: str) -> Any:
        """Verify the replaced local stream, without creating another source.

        Metadata alone is not frame proof. Decode one bounded JPEG in memory;
        never return subprocess stderr or the private Xiaomi source URL.
        """
        from PIL import Image

        from .models import XiaomiQualityProfile

        ffprobe, ffmpeg = bundled_binary("ffprobe"), bundled_binary("ffmpeg")
        if not ffprobe or not ffmpeg:
            raise RuntimeError("內附 FFmpeg／FFprobe 不可用，尚未驗證新畫質。")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", stream_name):
            raise ValueError("串流識別格式不正確。")
        url = f"rtsp://127.0.0.1:{self.rtsp_port}/{stream_name}"
        common = ["-v", "error", "-rtsp_transport", "tcp", "-timeout", "10000000"]
        try:
            probe = subprocess.run(
                [ffprobe, *common, "-select_streams", "v:0", "-show_entries",
                 "stream=width,height,codec_name,avg_frame_rate", "-of", "json", url],
                capture_output=True, timeout=12, check=False, shell=False,
            )
            streams = json.loads(probe.stdout).get("streams", []) if probe.returncode == 0 else []
            video = streams[0] if streams else {}
            width, height = int(video.get("width", 0)), int(video.get("height", 0))
            if width <= 0 or height <= 0:
                raise ValueError("missing_video")
            frame = subprocess.run(
                [ffmpeg, *common, "-i", url, "-map", "0:v:0", "-an", "-frames:v", "1",
                 "-vf", "scale=w='min(320,iw)':h=-2", "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1"],
                capture_output=True, timeout=12, check=False, shell=False,
            )
            if frame.returncode != 0 or not frame.stdout or len(frame.stdout) > 2 * 1024 * 1024:
                raise ValueError("invalid_frame")
            with Image.open(io.BytesIO(frame.stdout)) as image:
                if image.format != "JPEG":
                    raise ValueError("invalid_format")
                image.verify()
            return XiaomiQualityProfile(
                profile_name, width, height, self._parse_rate(video.get("avg_frame_rate")),
                str(video.get("codec_name") or "") or None, None, True,
            )
        except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired):
            raise RuntimeError("新畫質尚未取得有效影格；請查看連線診斷。") from None

    def remove_stream(self, stream_name: str) -> None:
        self.client.delete_stream(stream_name)

    def stream_status(self, stream_name: str) -> dict[str, Any] | str:
        return self.client.stream_status(stream_name)
