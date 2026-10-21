"""以 FFprobe 實測結果選擇最高且穩定的畫質。"""

from __future__ import annotations

from collections.abc import Iterable

from .models import XiaomiQualityProfile


def choose_highest_stable_profile(profiles: Iterable[XiaomiQualityProfile]) -> XiaomiQualityProfile | None:
    stable = [profile for profile in profiles if profile.stable and profile.width and profile.height]
    return max(stable, key=lambda item: (item.pixels, item.fps or 0.0)) if stable else None


def probe_profile_names() -> tuple[str, ...]:
    return ("subtype=auto", "subtype=sd", "subtype=hd", *tuple(f"subtype={index}" for index in range(6)))
