import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lpha_autofill import AutofillAbort, LABELS, ORDER, VALUES, WindowsBackend, fill, unique_controls, require_session_process, structure_summary


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def sleep(self, delay):
        self.now += delay


class FakeBackend:
    def __init__(self):
        self.values = dict.fromkeys(ORDER, "")
        self.current = "lpha"
        self.events = []
        self.escaped = False
        self.wrong_tab = False
        self.lose_after = None
        self.partial = False

    def check(self, field=None):
        if self.lose_after is not None and len(self.events) >= self.lose_after:
            raise AutofillAbort("FOCUS_LOST")
        if field is not None and self.current != field:
            raise AutofillAbort("UNEXPECTED_FIELD_FOCUS")

    def escape_down(self):
        return self.escaped

    def preflight(self):
        pass

    def focused(self, field):
        return self.current == field

    def read(self, field):
        return self.values[field]

    def prepare_combo(self, check):
        check()
        self.events.append("prepare")
        self.values["lpha"] = ""

    def character(self, character):
        self.events.append((self.current, character))
        if not self.partial:
            self.values[self.current] += character

    def tab(self):
        self.events.append("Tab")
        self.current = "unexpected" if self.wrong_tab else ORDER[ORDER.index(self.current) + 1]


class AutofillTests(unittest.TestCase):
    def run_fill(self, backend, cancelled=lambda: False):
        clock = Clock()
        return fill(backend, cancelled, clock, clock.sleep)

    def test_exact_fields_three_tabs_no_submit_or_final_tab(self):
        backend = FakeBackend()
        self.assertEqual(self.run_fill(backend), "FILLED_NOT_SUBMITTED")
        self.assertEqual(backend.values, VALUES)
        self.assertEqual(backend.events.count("Tab"), 3)
        self.assertEqual(backend.current, "npi")
        self.assertEqual(len([event for event in backend.events if isinstance(event, tuple)]), sum(map(len, VALUES.values())))

    def test_existing_draft_aborts_before_any_key(self):
        for field in ORDER[1:]:
            with self.subTest(field=field):
                backend = FakeBackend()
                backend.values[field] = "private draft"
                with self.assertRaisesRegex(AutofillAbort, "EXISTING_CONTENT"):
                    self.run_fill(backend)
                self.assertEqual(backend.events, [])

    def test_wrong_start_focus_no_input(self):
        backend = FakeBackend()
        backend.current = "name"
        with self.assertRaisesRegex(AutofillAbort, "UNEXPECTED_FIELD_FOCUS"):
            self.run_fill(backend)
        self.assertEqual(backend.events, [])

    def test_bad_tab_order_stops_before_typing_wrong_field(self):
        backend = FakeBackend()
        backend.wrong_tab = True
        with self.assertRaisesRegex(AutofillAbort, "NAVIGATION_OR_READBACK_FAILED"):
            self.run_fill(backend)
        self.assertEqual(backend.events.count("Tab"), 1)
        self.assertEqual(backend.values["name"], "")

    def test_escape_no_input(self):
        backend = FakeBackend()
        backend.escaped = True
        with self.assertRaisesRegex(AutofillAbort, "CANCELLED"):
            self.run_fill(backend)
        self.assertFalse(backend.events)

    def test_cancel_mid_entry_preserves_partial_no_retry(self):
        backend = FakeBackend()
        with self.assertRaisesRegex(AutofillAbort, "CANCELLED"):
            self.run_fill(backend, lambda: len(backend.events) >= 4)
        self.assertEqual(backend.values["lpha"], "Veg")
        self.assertEqual(backend.events.count("prepare"), 1)
        self.assertNotIn("Tab", backend.events)

    def test_focus_lost_mid_entry_preserves_partial(self):
        backend = FakeBackend()
        backend.lose_after = 4
        with self.assertRaisesRegex(AutofillAbort, "FOCUS_LOST"):
            self.run_fill(backend)
        self.assertEqual(backend.values["lpha"], "Veg")

    def test_incorrect_readback_never_advances(self):
        backend = FakeBackend()
        backend.partial = True
        with self.assertRaisesRegex(AutofillAbort, "NAVIGATION_OR_READBACK_FAILED"):
            self.run_fill(backend)
        self.assertNotIn("Tab", backend.events)

    def test_already_selected_dropdown_is_not_retyped(self):
        backend = FakeBackend()
        backend.values["lpha"] = VALUES["lpha"]
        self.run_fill(backend)
        self.assertNotIn("prepare", backend.events)

    def test_timeout_cannot_succeed(self):
        backend = FakeBackend()
        clock = Clock()
        def delayed_preflight():
            clock.now = 31
        backend.preflight = delayed_preflight
        with self.assertRaisesRegex(AutofillAbort, "TIMEOUT"):
            fill(backend, lambda: False, clock, clock.sleep)
        self.assertFalse(backend.events)


def control(field, index):
    return SimpleNamespace(element_info=SimpleNamespace(
        name=LABELS[field], control_type="ComboBox" if field == "lpha" else "Edit",
        runtime_id=(index,), element=SimpleNamespace(CurrentLabeledBy=None)))


class SelectorTests(unittest.TestCase):
    def test_structure_report_contains_counts_not_private_names_or_values(self):
        controls = [control(field, index) for index, field in enumerate(ORDER)]
        controls.append(control("name", 99))
        controls[-1].element_info.name = "PRIVATE PATIENT NAME"
        summary = structure_summary(controls, LABELS)
        self.assertEqual(summary["node_count"], 5)
        self.assertEqual(summary["roles"]["Edit"], 4)
        self.assertEqual(summary["expected_label_matches"], dict.fromkeys(ORDER, 1))
        self.assertNotIn("PRIVATE", str(summary))

    def test_exact_unique_labels_no_coordinates(self):
        controls = [control(field, index) for index, field in enumerate(ORDER)]
        controls[1].element_info.name = " Printed Name: "
        self.assertEqual(tuple(unique_controls(controls, LABELS)), ORDER)

    def test_accessible_labeled_by(self):
        controls = [control(field, index) for index, field in enumerate(ORDER)]
        controls[1].element_info.name = ""
        controls[1].element_info.element.CurrentLabeledBy = SimpleNamespace(CurrentName="Printed Name")
        self.assertEqual(len(unique_controls(controls, LABELS)), 4)

    def test_missing_or_duplicate_aborts(self):
        controls = [control(field, index) for index, field in enumerate(ORDER)]
        for candidates in (controls[:-1], controls + [control("name", 99)]):
            with self.assertRaisesRegex(AutofillAbort, "FIELDS_NOT_UNIQUE_OR_EXPOSED"):
                unique_controls(candidates, LABELS)

    def test_no_substring_or_button_matching(self):
        controls = [control(field, index) for index, field in enumerate(ORDER)]
        controls[0].element_info.control_type = "Button"
        with self.assertRaises(AutofillAbort):
            unique_controls(controls, LABELS)
        controls[0].element_info.control_type = "ComboBox"
        controls[0].element_info.name = "LPHA sign and submit"
        with self.assertRaises(AutofillAbort):
            unique_controls(controls, LABELS)


class WindowsSafetyTests(unittest.TestCase):
    def test_readonly_inspection_never_reads_values_or_sends_input(self):
        backend = self.backend()
        backend.labels = LABELS
        backend.window.descendants = lambda: [control(field, index) for index, field in enumerate(ORDER)]
        backend.send_key = Mock(side_effect=AssertionError("no input"))
        backend.read = Mock(side_effect=AssertionError("no values"))
        backend.preflight = Mock(side_effect=AssertionError("no input preflight"))
        summary = backend.inspect_structure()
        self.assertEqual(summary["node_count"], 4)
        backend.send_key.assert_not_called()
        backend.read.assert_not_called()
        backend.preflight.assert_not_called()

    def test_current_and_legacy_citrix_session_processes(self):
        for name in ("Citrix.DesktopViewer.App.exe", "WFICA32.EXE", "CDViewer.exe"):
            with self.subTest(name=name):
                require_session_process("C:\\Program Files (x86)\\Citrix\\ICA Client\\" + name)

    def test_citrix_launchers_and_unrelated_processes_are_not_sessions(self):
        for name in ("SelfService.exe", "wfcrun32.exe", "Citrix.EngineUI.App.exe", "Taskmgr.exe", "vivaldi.exe",
                     "Citrix.DesktopViewer.App.exe.bak"):
            with self.subTest(name=name), self.assertRaisesRegex(AutofillAbort, "NOT_CITRIX_SESSION"):
                require_session_process(name)

    def backend(self):
        backend = WindowsBackend.__new__(WindowsBackend)
        backend.hwnd = 101
        backend.pid = 20
        backend.layout = 7
        backend.window_thread = 30
        backend.window = SimpleNamespace(process_id=lambda: 20)
        backend.controls = {}
        backend.ids = {}
        backend.api = SimpleNamespace(GetForegroundWindow=lambda: 101, GetAsyncKeyState=lambda key: 0,
                                      GetKeyState=lambda key: 0, GetKeyboardLayout=lambda thread: 7)
        return backend

    def test_wrong_window_aborts(self):
        backend = self.backend()
        backend.api.GetForegroundWindow = lambda: 102
        with self.assertRaisesRegex(AutofillAbort, "FOCUS_LOST"):
            backend.check()

    def test_modifiers_and_caps_abort(self):
        backend = self.backend()
        backend.api.GetAsyncKeyState = lambda key: 0x8000 if key == 0x11 else 0
        with self.assertRaisesRegex(AutofillAbort, "MODIFIERS_HELD"):
            backend.check()
        backend.api.GetAsyncKeyState = lambda key: 0
        backend.api.GetKeyState = lambda key: 1
        with self.assertRaisesRegex(AutofillAbort, "CAPS_LOCK_ON"):
            backend.check()

    def test_target_keyboard_layout_must_match(self):
        backend = self.backend()
        backend.api.GetKeyboardLayout = lambda thread: 8 if thread == 30 else 7
        with self.assertRaisesRegex(AutofillAbort, "KEYBOARD_LAYOUT_MISMATCH"):
            backend.check()

    def test_resizing_does_not_change_identity(self):
        backend = self.backend()
        field = control("name", 1)
        field.top_level_parent = lambda: SimpleNamespace(handle=101)
        field.is_visible = lambda: True
        field.is_enabled = lambda: True
        backend.controls = {"name": field}
        backend.ids = {"name": (1,)}
        backend.check()
        field.rectangle = lambda: (_ for _ in ()).throw(AssertionError("coordinates forbidden"))
        backend.check()
        field.element_info.runtime_id = (2,)
        with self.assertRaisesRegex(AutofillAbort, "CONTROL_CHANGED"):
            backend.check()

    def test_key_failure_releases_shift_and_key(self):
        backend = self.backend()
        backend.mapping = {"V": (0x56, True)}
        events = []
        def send(key, key_up=False):
            events.append((key, key_up))
            if key == 0x56 and not key_up:
                raise OSError("input denied")
        backend.send_key = send
        with self.assertRaises(OSError):
            backend.character("V")
        self.assertEqual(events[-2:], [(0x56, True), (0x10, True)])

    def test_mapping_or_readonly_fail_before_mutation(self):
        for readonly in (False, True):
            backend = self.backend()
            controls = [control(field, index) for index, field in enumerate(ORDER)]
            for item in controls:
                item.element_info.element.CurrentIsPassword = False
                item.is_editable = lambda: not readonly
            backend.window.descendants = lambda: controls
            backend.focused = lambda field: True
            backend.check = lambda field=None: None
            backend.read = lambda field: ""
            backend.labels = LABELS
            backend.api.VkKeyScanExW = lambda character, layout: -1
            backend.send_key = Mock()
            expected = "READ_ONLY_CONTROL" if readonly else "UNSUPPORTED_KEYBOARD_MAPPING"
            with self.assertRaisesRegex(AutofillAbort, expected):
                backend.preflight()
            backend.send_key.assert_not_called()

    def test_combo_prepare_and_tab_never_enter(self):
        for editable in (False, True):
            backend = self.backend()
            backend.controls = {"lpha": SimpleNamespace(is_editable=lambda: editable)}
            backend.send_key = Mock()
            backend.prepare_combo(lambda: None)
            backend.tab()
            keys = [call.args[0] for call in backend.send_key.call_args_list]
            self.assertNotIn(0x0D, keys)
            self.assertIn(0x09, keys)

    def test_non_string_value_is_not_empty(self):
        backend = self.backend()
        backend.controls = {"name": SimpleNamespace(get_value=lambda: None)}
        with self.assertRaisesRegex(AutofillAbort, "VALUE_UNAVAILABLE"):
            backend.read("name")


class IntegrationTests(unittest.TestCase):
    def config(self):
        return {"hotkey": {"modifiers": ["ctrl", "alt"], "key": "v"},
                "target_hotkey": {"modifiers": ["ctrl", "alt", "shift"], "key": "v"},
                "monitor_sleep_hotkey": {"modifiers": ["ctrl", "alt", "shift"], "key": "m"},
                "lpha_hotkey": {"modifiers": ["ctrl", "alt"], "key": "l"},
                "lpha_inspect_hotkey": {"modifiers": ["ctrl", "alt", "shift"], "key": "l"}}

    def test_readonly_worker_never_calls_autofill(self):
        import imagepastor as app
        class InlineThread:
            def __init__(self, target, **kwargs):
                self.target = target
            def start(self):
                self.target()
        backend = Mock()
        backend.inspect_structure.return_value = {"node_count": 2}
        with patch.object(app.threading, "Thread", InlineThread), patch.object(app, "active_config", self.config()), \
                patch.object(app, "monitor_sleep_guard_enabled", False), \
                patch.object(app, "wait_for_hotkey_release", return_value=True), \
                patch.object(app, "WindowsBackend", return_value=backend), \
                patch.object(app, "notify_lpha"), patch.object(app, "log"), \
                patch.object(app, "fill_lpha") as autofill:
            app.start_lpha_autofill(from_hotkey=True, inspect_only=True)
        self.assertEqual(backend.inspect_structure.call_count, 2)
        autofill.assert_not_called()
        self.assertFalse(app.lpha_lock.locked())

    def test_readonly_hotkey_cannot_conflict_with_autofill(self):
        import imagepastor as app
        config = self.config()
        config["lpha_inspect_hotkey"] = config["lpha_hotkey"]
        with patch.object(app.user32, "RegisterHotKey") as register, patch.object(app, "log"):
            self.assertFalse(app.register_lpha_hotkey(config, inspect_only=True))
        register.assert_not_called()

    def test_hotkey_registration_has_unique_id(self):
        import imagepastor as app
        with patch.object(app.user32, "RegisterHotKey", return_value=1) as register, patch.object(app, "log"):
            self.assertTrue(app.register_lpha_hotkey(self.config()))
        register.assert_called_once_with(None, app.LPHA_HOTKEY_ID, app.MOD_NOREPEAT | app.MOD_CONTROL | app.MOD_ALT, ord("L"))

    def test_conflict_or_bad_config_does_not_register_or_break_existing(self):
        import imagepastor as app
        for hotkey in ({"modifiers": ["control", "alt"], "key": "v"}, {"modifiers": ["invalid"], "key": "l"}):
            config = self.config()
            config["lpha_hotkey"] = hotkey
            with patch.object(app.user32, "RegisterHotKey") as register, patch.object(app, "log"):
                self.assertFalse(app.register_lpha_hotkey(config))
            register.assert_not_called()

    def test_os_hotkey_conflict_is_optional(self):
        import imagepastor as app
        with patch.object(app.user32, "RegisterHotKey", return_value=0), patch.object(app, "log"):
            self.assertFalse(app.register_lpha_hotkey(self.config()))

    def test_second_request_cancels_without_second_worker(self):
        import imagepastor as app
        app.lpha_lock.acquire()
        try:
            with patch.object(app.threading, "Thread") as thread, patch.object(app, "log"):
                app.start_lpha_autofill(from_hotkey=True)
                thread.assert_not_called()
                self.assertTrue(app.lpha_cancel.is_set())
        finally:
            app.lpha_lock.release()
            app.lpha_cancel.clear()

    def test_worker_hides_accessibility_exception_and_releases_lock(self):
        import imagepastor as app
        class InlineThread:
            def __init__(self, target, **kwargs):
                self.target = target
            def start(self):
                self.target()
        with patch.object(app.threading, "Thread", InlineThread), patch.object(app, "active_config", self.config()), \
                patch.object(app, "monitor_sleep_guard_enabled", False), \
                patch.object(app, "wait_for_hotkey_release", return_value=True), \
                patch.object(app, "WindowsBackend", side_effect=RuntimeError("PRIVATE PATIENT DRAFT")), \
                patch.object(app, "notify_lpha"), patch.object(app, "log") as log:
            app.start_lpha_autofill(from_hotkey=True)
        self.assertFalse(app.lpha_lock.locked())
        self.assertNotIn("PRIVATE PATIENT", str(log.call_args_list))
        self.assertIn("ACCESSIBILITY_OR_INPUT_UNAVAILABLE", str(log.call_args_list))


if __name__ == "__main__":
    unittest.main()
