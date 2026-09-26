import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import imagepastor


class ChatGptAddressTests(unittest.TestCase):
    def test_capture_hotkey_includes_all_physical_keys_to_release(self):
        self.assertEqual(
            imagepastor.hotkey_virtual_keys({"modifiers": ["ctrl", "alt"], "key": "v"}),
            [imagepastor.VK_V, imagepastor.VK_CONTROL, imagepastor.VK_MENU],
        )

    def test_extracts_host_when_chrome_omits_scheme(self):
        self.assertEqual(
            imagepastor.url_host("chatgpt.com/c/example"),
            "chatgpt.com",
        )

    def test_allows_exact_configured_host(self):
        self.assertTrue(
            imagepastor.host_is_allowed("chatgpt.com", ["chatgpt.com"])
        )

    def test_allows_subdomain(self):
        self.assertTrue(
            imagepastor.host_is_allowed("workspace.chatgpt.com", ["chatgpt.com"])
        )

    def test_rejects_lookalike_domain(self):
        self.assertFalse(
            imagepastor.host_is_allowed("chatgpt.com.example.org", ["chatgpt.com"])
        )

    def test_rejects_suffix_without_domain_boundary(self):
        self.assertFalse(
            imagepastor.host_is_allowed("fakechatgpt.com", ["chatgpt.com"])
        )

    def test_accepts_vivaldi_address_field(self):
        class Control:
            def __init__(self, name, class_name, value):
                self.element_info = type("Info", (), {"name": name, "class_name": class_name})()
                self._value = value

            def get_value(self):
                return self._value

        class Window:
            def descendants(self, control_type):
                if control_type != "Edit":
                    raise AssertionError(f"Unexpected control type: {control_type}")
                return [
                    Control("Search", "TextField", "not-chatgpt.example"),
                    Control("Address Field", "TextField", "https://chatgpt.com/c/example"),
                ]

        self.assertTrue(
            imagepastor.chromium_window_has_allowed_address(Window(), ["chatgpt.com"])
        )

    def test_ignores_non_address_edit_fields(self):
        class Control:
            element_info = type("Info", (), {"name": "Chat with ChatGPT", "class_name": "TextField"})()

            def get_value(self):
                return "https://chatgpt.com/c/example"

        class Window:
            def descendants(self, control_type):
                return [Control()]

        self.assertFalse(
            imagepastor.chromium_window_has_allowed_address(Window(), ["chatgpt.com"])
        )

    def test_prefers_the_selected_chatgpt_tile_composer(self):
        class Info:
            def __init__(self, control_type, name):
                self.control_type = control_type
                self.name = name

        class Control:
            def __init__(self, control_type, name, parent=None, selected=False):
                self.element_info = Info(control_type, name)
                self._parent = parent
                self._selected = selected

            def parent(self):
                return self._parent

            def is_visible(self):
                return True

            def is_enabled(self):
                return True

            def is_selected(self):
                return self._selected

        left_document = Control("Document", "ChatGPT - Treasure Hunters")
        right_document = Control("Document", "Other ChatGPT Chat")
        left_composer = Control("Edit", "Chat with ChatGPT", left_document)
        right_composer = Control("Edit", "Chat with ChatGPT", right_document)

        class Window:
            def descendants(self, control_type):
                if control_type == "Edit":
                    return [left_composer, right_composer]
                if control_type == "TabItem":
                    return [Control("TabItem", "ChatGPT - Treasure Hunters", selected=True)]
                return []

        self.assertIs(
            imagepastor.find_chatgpt_composer(Window(), "ChatGPT - Treasure Hunters"),
            left_composer,
        )


class MonitorControlTests(unittest.TestCase):
    def setUp(self):
        imagepastor.monitor_sleep_guard_enabled = False
        imagepastor.monitor_sleep_guard_cancel_event = None

    def tearDown(self):
        imagepastor.monitor_sleep_guard_enabled = False
        event = imagepastor.monitor_sleep_guard_cancel_event
        imagepastor.monitor_sleep_guard_cancel_event = None
        if event is not None:
            event.set()

    def test_hidden_control_window_lifecycle(self):
        imagepastor.configure_win32_signatures()
        hwnd = imagepastor.create_monitor_control_window()
        try:
            self.assertTrue(imagepastor.user32.IsWindow(hwnd))
        finally:
            imagepastor.destroy_monitor_control_window()

    def test_cancelled_guard_cannot_queue_monitor_off(self):
        cancel_event = threading.Event()
        cancel_event.set()
        imagepastor.monitor_sleep_guard_enabled = True
        imagepastor.monitor_sleep_guard_cancel_event = cancel_event

        with patch.object(imagepastor, "post_monitor_power") as post_power:
            self.assertFalse(imagepastor.post_guard_monitor_off(cancel_event))
            post_power.assert_not_called()

    def test_disabling_queues_monitor_on_after_last_off(self):
        cancel_event = threading.Event()
        imagepastor.monitor_sleep_guard_enabled = True
        imagepastor.monitor_sleep_guard_cancel_event = cancel_event
        queued_states = []

        with patch.object(
            imagepastor,
            "post_monitor_power",
            side_effect=lambda state: queued_states.append(state) or True,
        ):
            self.assertTrue(imagepastor.post_guard_monitor_off(cancel_event))
            imagepastor.set_monitor_sleep_guard(False, {})

        self.assertEqual(
            queued_states,
            [imagepastor.MONITOR_POWER_OFF, imagepastor.MONITOR_POWER_ON],
        )
        self.assertTrue(cancel_event.is_set())


if __name__ == "__main__":
    unittest.main()
