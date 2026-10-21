"""User choices, not a promise of resolution or a diagnostic sweep."""

LIVE_PROFILE_OPTIONS = (
    "subtype=1", "subtype=2", "subtype=3", "subtype=auto",
    "subtype=sd", "subtype=hd", "subtype=0", "subtype=4", "subtype=5",
)
PROFILE_LABELS = {
    "subtype=1": "1（通常 SD）", "subtype=2": "2（通常 HD）",
    "subtype=3": "3（部分新機高畫質）", "subtype=auto": "AUTO（自動）",
    "subtype=sd": "SD（協定別名）", "subtype=hd": "HD（依 go2rtc 機型映射）",
    "subtype=0": "0（自動）", "subtype=4": "4（進階，需實測）",
    "subtype=5": "5（進階，需實測）",
}
