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

            def get_value(self):
                return "https://chatgpt.com/c/" + self.element_info.name.replace(" ", "-")

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
        execution_state = patch.object(
            imagepastor.kernel32, "SetThreadExecutionState", return_value=1
        )
        self.execution_state = execution_state.start()
        self.addCleanup(execution_state.stop)
        imagepastor.monitor_sleep_guard_enabled = False
        imagepastor.monitor_sleep_guard_cancel_event = None
        imagepastor.monitor_sleep_once_cancel_event = None
        imagepastor.monitor_power_generation = 0
        imagepastor.monitor_power_desired = imagepastor.MONITOR_POWER_ON
        imagepastor.monitor_power_pending = None
        imagepastor.monitor_display_state = None
        imagepastor.monitor_wake_until = 0.0

    def tearDown(self):
        imagepastor.monitor_sleep_guard_enabled = False
        event = imagepastor.monitor_sleep_guard_cancel_event
        imagepastor.monitor_sleep_guard_cancel_event = None
        if event is not None:
            event.set()
        if imagepastor.monitor_sleep_once_cancel_event is not None:
            imagepastor.monitor_sleep_once_cancel_event.set()

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

    def test_cancelled_queued_off_is_discarded_at_execution(self):
        imagepastor.monitor_power_generation = 2
        imagepastor.monitor_power_desired = imagepastor.MONITOR_POWER_ON
        with patch.object(imagepastor.user32, "DefWindowProcW") as power_call:
            imagepastor.apply_monitor_power(123, 1, imagepastor.MONITOR_POWER_OFF)
            power_call.assert_not_called()

    def test_already_off_display_is_not_power_cycled_again(self):
        imagepastor.monitor_power_desired = imagepastor.MONITOR_POWER_OFF
        imagepastor.monitor_display_state = 0
        with patch.object(imagepastor.user32, "DefWindowProcW") as power_call:
            imagepastor.apply_monitor_power(123, 0, imagepastor.MONITOR_POWER_OFF)
            power_call.assert_not_called()

    def test_extra_hotkey_presses_during_wake_cannot_enable_guard(self):
        imagepastor.monitor_sleep_guard_enabled = True
        imagepastor.monitor_sleep_guard_cancel_event = threading.Event()
        with patch.object(imagepastor, "post_monitor_power") as post_power:
            with patch.object(imagepastor.time, "monotonic", return_value=100.0):
                imagepastor.toggle_monitor_sleep_guard({})
                for _ in range(5):
                    imagepastor.toggle_monitor_sleep_guard({})
        self.assertFalse(imagepastor.monitor_sleep_guard_enabled)
        post_power.assert_called_once_with(imagepastor.MONITOR_POWER_ON)
        self.execution_state.assert_called_once_with(
            imagepastor.ES_SYSTEM_REQUIRED | imagepastor.ES_DISPLAY_REQUIRED
        )

    def test_wake_cancels_delayed_one_shot_sleep_even_with_guard_disabled(self):
        cancel_event = threading.Event()
        imagepastor.monitor_sleep_once_cancel_event = cancel_event
        imagepastor.monitor_power_desired = imagepastor.MONITOR_POWER_OFF
        with patch.object(imagepastor, "post_monitor_power") as post_power:
            imagepastor.toggle_monitor_sleep_guard({})
            self.assertFalse(imagepastor.sleep_monitors(0, cancel_event, 0))
        post_power.assert_called_once_with(imagepastor.MONITOR_POWER_ON)
        self.assertTrue(cancel_event.is_set())
        self.assertFalse(imagepastor.monitor_sleep_guard_enabled)

    def test_duplicate_off_commands_are_coalesced(self):
        imagepastor.monitor_power_desired = imagepastor.MONITOR_POWER_OFF
        with patch.object(imagepastor, "monitor_control_hwnd", 123):
            with patch.object(imagepastor.user32, "IsWindow", return_value=True):
                with patch.object(imagepastor.user32, "PostMessageW", return_value=True) as queue:
                    for _ in range(10):
                        self.assertTrue(imagepastor.post_monitor_power(imagepastor.MONITOR_POWER_OFF))
        queue.assert_called_once()

    def test_unlock_stops_guard_instead_of_turning_display_off_again(self):
        cancel_event = threading.Event()
        imagepastor.monitor_sleep_guard_enabled = True
        imagepastor.monitor_sleep_guard_cancel_event = cancel_event
        imagepastor.monitor_power_desired = imagepastor.MONITOR_POWER_OFF
        with patch.object(imagepastor, "post_monitor_power") as post_power:
            imagepastor.wake_monitors_after_unlock()
        self.assertTrue(cancel_event.is_set())
        self.assertFalse(imagepastor.monitor_sleep_guard_enabled)
        post_power.assert_called_once_with(imagepastor.MONITOR_POWER_ON)

    def test_unlock_with_monitors_already_awake_has_no_power_command(self):
        with patch.object(imagepastor, "post_monitor_power") as post_power:
            imagepastor.wake_monitors_after_unlock()
        post_power.assert_not_called()

    def test_wake_requests_display_awake_and_releases_it_after_grace(self):
        with patch.object(imagepastor.user32, "DefWindowProcW") as power_call:
            with patch.object(imagepastor.user32, "SetTimer", return_value=1):
                with patch.object(imagepastor.user32, "KillTimer") as kill_timer:
                    with patch.object(imagepastor.kernel32, "SetThreadExecutionState", return_value=1) as awake:
                        with patch.object(imagepastor.time, "monotonic", return_value=100.0):
                            imagepastor.monitor_display_state = 1
                            imagepastor.apply_monitor_power(123, 0, imagepastor.MONITOR_POWER_ON)
                            imagepastor.finish_monitor_wake(123)
                            kill_timer.assert_not_called()
                        with patch.object(imagepastor.time, "monotonic", return_value=111.0):
                            imagepastor.finish_monitor_wake(123)
        self.assertEqual(
            [call.args[0] for call in awake.call_args_list],
            [imagepastor.ES_CONTINUOUS | imagepastor.ES_SYSTEM_REQUIRED | imagepastor.ES_DISPLAY_REQUIRED,
             imagepastor.ES_CONTINUOUS],
        )
        power_call.assert_called_once_with(
            123, imagepastor.WM_SYSCOMMAND, imagepastor.SC_MONITORPOWER, imagepastor.MONITOR_POWER_ON
        )
        kill_timer.assert_called_once()

    def test_slow_power_driver_does_not_block_wake_request(self):
        entered = threading.Event()
        release = threading.Event()
        woke = threading.Event()
        original = imagepastor.user32.DefWindowProcW

        def slow_power_call(hwnd, message, wparam, lparam):
            if message == imagepastor.WM_SYSCOMMAND and wparam == imagepastor.SC_MONITORPOWER:
                if lparam == imagepastor.MONITOR_POWER_OFF:
                    entered.set()
                    release.wait(3.0)
                else:
                    woke.set()
                return 0
            return original(hwnd, message, wparam, lparam)

        imagepastor.configure_win32_signatures()
        with patch.object(imagepastor.user32, "DefWindowProcW", side_effect=slow_power_call):
            with patch.object(imagepastor.kernel32, "SetThreadExecutionState", return_value=1):
                imagepastor.start_monitor_control_thread()
                try:
                    # Force the test off path without touching the real monitor power state.
                    imagepastor.monitor_display_state = None
                    imagepastor.monitor_power_desired = imagepastor.MONITOR_POWER_OFF
                    self.assertTrue(imagepastor.post_monitor_power(imagepastor.MONITOR_POWER_OFF))
                    self.assertTrue(entered.wait(2.0))
                    imagepastor.set_monitor_sleep_guard(False, {})
                    self.assertEqual(imagepastor.monitor_power_desired, imagepastor.MONITOR_POWER_ON)
                    self.assertFalse(woke.is_set())
                    release.set()
                    self.assertTrue(woke.wait(2.0))
                finally:
                    release.set()
                    imagepastor.stop_monitor_control_thread()
                self.assertFalse(imagepastor.monitor_control_thread.is_alive())


if __name__ == "__main__":
    unittest.main()
