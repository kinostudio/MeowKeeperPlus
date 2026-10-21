"""一個 Telegram Bot、多 Chat ID、權限與通知重試。"""

from .telegram import TelegramNotificationError, TelegramNotifier, TelegramSendResult

__all__ = ["TelegramNotificationError", "TelegramNotifier", "TelegramSendResult"]
from .dispatcher import NotificationDispatcher

__all__ = ["NotificationDispatcher"]
