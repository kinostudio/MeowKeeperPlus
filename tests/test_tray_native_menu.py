import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from meowkeeper.tray.windows_menu import responsive_icon_class


class NativeMenuTests(unittest.TestCase):
    def test_same_owner_deferred_updates_bounded_notices_and_null_message(self):
        class Base:
            def __init__(self):
                self._hwnd = 101
                self._menu_hwnd = 202
                self._message_handlers = {}
                self._menu_handle = (303, [Mock()])
                self.updates = 0
                self.messages = []
            def _on_notify(self, *args):
                pass
            def _update_menu(self):
                self.updates += 1
            def notify(self, message, title=None):
                self.messages.append(message)
        api = SimpleNamespace(WM_USER=1024, WM_RBUTTONUP=517, TPM_RIGHTALIGN=8,
                              TPM_BOTTOMALIGN=32, TPM_RETURNCMD=256,
                              SetForegroundWindow=Mock(), GetCursorPos=Mock(), PostMessage=Mock())
        icon = responsive_icon_class(Base, api)()
        loader = Mock(return_value="image")
        def track(*args):
            self.assertEqual(args[4], 202)
            api.SetForegroundWindow.assert_called_once_with(202)
            icon._update_menu()
            self.assertEqual(icon.updates, 0)
            icon.queue_status("ready", "正常", loader)
            for i in range(100):
                icon.notify(str(i))
            icon._apply_pending()
            loader.assert_not_called()
            self.assertEqual(icon.messages, [])
            self.assertEqual(len(icon._notices), 8)
            return 1
        api.TrackPopupMenuEx = Mock(side_effect=track)
        icon._on_notify(0, 517)
        api.PostMessage.assert_any_call(202, 0, 0, 0)
        self.assertFalse(icon._menu_open)
        self.assertEqual(icon.updates, 1)
        icon._apply_pending()
        loader.assert_called_once_with("ready")
        self.assertEqual(icon.title, "正常")
        self.assertEqual(len(icon.messages), 8)
        icon._menu_handle[1][0].assert_called_once_with(icon)

    def test_cancel_and_exception_release_popup(self):
        class Base:
            def __init__(self):
                self._hwnd = 101
                self._menu_hwnd = 202
                self._message_handlers = {}
                self._menu_handle = (303, [Mock()])
            def _on_notify(self, *args):
                pass
        api = SimpleNamespace(WM_USER=1024, WM_RBUTTONUP=517, TPM_RIGHTALIGN=8,
                              TPM_BOTTOMALIGN=32, TPM_RETURNCMD=256,
                              SetForegroundWindow=Mock(), GetCursorPos=Mock(), PostMessage=Mock(),
                              TrackPopupMenuEx=Mock(return_value=0))
        icon = responsive_icon_class(Base, api)()
        icon._on_notify(0, 517)
        icon._menu_handle[1][0].assert_not_called()
        api.TrackPopupMenuEx.side_effect = OSError("test")
        with self.assertRaises(OSError):
            icon._on_notify(0, 517)
        self.assertFalse(icon._menu_open)
        api.PostMessage.assert_any_call(202, 0, 0, 0)
