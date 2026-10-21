"""Keep static Windows popup menus and Shell updates on the native message loop."""
from __future__ import annotations

import ctypes
import threading
from collections import deque
from ctypes import wintypes


def responsive_icon_class(base, api=None):
    """Adapt pystray's Windows backend without modifying installed dependencies."""
    if not hasattr(base, "_on_notify"):
        return base  # Other platforms and test adapters keep their own backend.
    if api is None:
        from pystray._util import win32 as api

    class ResponsiveIcon(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._menu_open = False
            self._pending_menu = False
            self._pending_status = None
            self._notices = deque(maxlen=8)
            self._ui_lock = threading.Lock()
            self._apply_message = api.WM_USER + 31
            self._message_handlers[self._apply_message] = self._apply_pending

        def _wake(self):
            if self._hwnd:
                api.PostMessage(self._hwnd, self._apply_message, 0, 0)

        def queue_status(self, kind, title, image_loader):
            with self._ui_lock:
                self._pending_status = (kind, title, image_loader)
            self._wake()

        def notify(self, message, title=None):
            with self._ui_lock:
                self._notices.append((message, title))
            self._wake()

        def _apply_pending(self, *_args):
            if self._menu_open:
                return  # TrackPopupMenu runs a nested message loop.
            with self._ui_lock:
                status, self._pending_status = self._pending_status, None
                notices = list(self._notices)
                self._notices.clear()
            if status:
                kind, title, loader = status
                self.title = title
                self.icon = loader(kind)
            for message, title in notices:
                super().notify(message, title)

        def _show(self):
            super()._show()
            self._wake()  # Flush notices queued before the HWND became ready.

        def _update_menu(self):
            if self._menu_open:
                self._pending_menu = True
                return
            super()._update_menu()

        def _on_notify(self, wparam, lparam):
            if lparam != api.WM_RBUTTONUP or not self._menu_handle:
                return super()._on_notify(wparam, lparam)
            if self._menu_open:
                return
            self._menu_open = True
            index = 0
            menu, callbacks = self._menu_handle
            try:
                # pystray's separate menu HWND uses DefWindowProc for native
                # menu/theme painting; the icon dispatcher swallows unhandled
                # messages. Using the icon HWND can leave a blank popup.
                owner = self._menu_hwnd
                api.SetForegroundWindow(owner)
                point = wintypes.POINT()
                api.GetCursorPos(ctypes.byref(point))
                index = api.TrackPopupMenuEx(
                    menu, api.TPM_RIGHTALIGN | api.TPM_BOTTOMALIGN | api.TPM_RETURNCMD,
                    point.x, point.y, owner, None,
                )
            finally:
                self._menu_open = False
                api.PostMessage(self._menu_hwnd, 0, 0, 0)  # WM_NULL: close popup lifecycle.
                if self._pending_menu:
                    self._pending_menu = False
                    self._update_menu()
                self._wake()
            if 0 < index <= len(callbacks):
                callbacks[index - 1](self)

    return ResponsiveIcon
