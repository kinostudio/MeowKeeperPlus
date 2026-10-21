"""小米 Mi Home／go2rtc bridge 的安全規劃介面。"""

from .device_discovery import Go2RtcXiaomiDiscovery
from .login_diagnostics import (
    LOGIN_CLASSIFICATIONS,
    XiaomiLoginClassification,
    XiaomiLoginDiagnostic,
    classify_login_flow_response,
    classify_login_response,
)
from .models import XiaomiDeviceIdentity, XiaomiSourceProfile
from .quality_probe import choose_highest_stable_profile
from .service import XiaomiBridgeService

__all__ = [
    "LOGIN_CLASSIFICATIONS",
    "Go2RtcXiaomiDiscovery",
    "XiaomiBridgeService",
    "XiaomiDeviceIdentity",
    "XiaomiLoginClassification",
    "XiaomiLoginDiagnostic",
    "XiaomiSourceProfile",
    "choose_highest_stable_profile",
    "classify_login_flow_response",
    "classify_login_response",
]
