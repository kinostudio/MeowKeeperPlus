"""Windows System Tray 的輕量 API client 層。"""

from .client import TrayApiClient, TrayApiError
from .single_instance import SingleInstanceLock

__all__ = ["SingleInstanceLock", "TrayApiClient", "TrayApiError"]
