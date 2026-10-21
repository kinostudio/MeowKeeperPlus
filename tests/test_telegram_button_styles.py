import json
import unittest
from unittest.mock import Mock

from meowkeeper.core.notifications.telegram import TelegramNotifier, _styled_reply_markup


class TelegramButtonStyleTests(unittest.TestCase):
    def test_inline_actions_use_semantic_colors_and_neutral_choices_stay_neutral(self) -> None:
        markup = {
            "inline_keyboard": [[
                {"text": "✅ 是貓咪", "callback_data": "cat:event:yes"},
                {"text": "❌ 不是貓咪", "callback_data": "cat:event:no"},
            ], [
                {"text": "不確定", "callback_data": "who:review:uncertain"},
                {"text": "確認提交", "callback_data": "tags:review:done"},
            ], [
                {"text": "回報軌跡", "callback_data": "trk:event:open"},
                {"text": "行為選擇", "callback_data": "fb:event:b:eating"},
            ]],
        }

        styled = _styled_reply_markup(markup)
        buttons = [button for row in styled["inline_keyboard"] for button in row]

        self.assertEqual([button.get("style") for button in buttons], [
            "success", "danger", None, "success", "primary", None,
        ])
        self.assertNotIn("style", markup["inline_keyboard"][0][0])

    def test_reply_keyboard_gets_mixed_styles_without_coloring_neutral_controls(self) -> None:
        markup = {"keyboard": [[
            {"text": "📷 即時截圖"},
            {"text": "通知"},
            {"text": "🔒 隱私模式"},
            {"text": "👁 恢復畫面"},
        ]], "resize_keyboard": True}

        styled = _styled_reply_markup(markup)
        buttons = styled["keyboard"][0]

        self.assertEqual([button.get("style") for button in buttons], [
            "primary", None, "danger", "success",
        ])
        self.assertTrue(styled["resize_keyboard"])
        self.assertEqual(markup["keyboard"][0][0], {"text": "📷 即時截圖"})

    def test_existing_style_and_callback_payload_are_preserved_in_sent_json(self) -> None:
        notifier = TelegramNotifier.__new__(TelegramNotifier)
        notifier._call = Mock(return_value={"result": {"message_id": 42}})
        markup = {"inline_keyboard": [[
            {"text": "已指定", "callback_data": "opaque:kept", "style": "primary"},
            {"text": "通知", "callback_data": "opaque:neutral"},
        ]]}

        notifier.send_message("123", "測試", reply_markup=markup)

        serialized = notifier._call.call_args.kwargs["fields"]["reply_markup"]
        sent_markup = json.loads(serialized)
        self.assertEqual(sent_markup["inline_keyboard"][0][0]["style"], "primary")
        self.assertEqual(sent_markup["inline_keyboard"][0][0]["callback_data"], "opaque:kept")
        self.assertNotIn("style", sent_markup["inline_keyboard"][0][1])
        self.assertNotIn("style", markup["inline_keyboard"][0][1])


if __name__ == "__main__":
    unittest.main()
