"""FastAPI Web Dashboard 的可選應用層。

核心媒體與 AI 不依賴本模組；若發布 runtime 尚未包含 Web extra，既有
CLI／24H 媒體功能仍可使用，而啟動 Web 時會給出繁體中文提示。
"""

from .auth import ApiAuthenticator, AuthError
from .events import EventHub
from .main import create_app
from .session import SessionManager
from .state import CoreStateFacade

__all__ = ["ApiAuthenticator", "AuthError", "CoreStateFacade", "EventHub", "SessionManager", "create_app"]
