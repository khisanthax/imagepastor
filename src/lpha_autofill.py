"""Fail-closed, keyboard-only LPHA autofill. Never submit or sign."""

import time
from pathlib import PureWindowsPath


VALUES = {
    "lpha": "Vega, Michael",
    "name": "michael vega",
    "credential": "lcsw",
    "npi": "1699090837",
}
LABELS = {"lpha": "LPHA", "name": "Printed Name", "credential": "Credential", "npi": "NPI#"}
ORDER = tuple(VALUES)
SESSION_PROCESSES = frozenset({"wfica32.exe", "cdviewer.exe", "citrix.desktopviewer.app.exe"})


class AutofillAbort(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require_session_process(path):
    if PureWindowsPath(path).name.lower() not in SESSION_PROCESSES:
        raise AutofillAbort("NOT_CITRIX_SESSION")


def fill(backend, cancelled, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + 30

    def check(field=None):
        if cancelled() or backend.escape_down():
            raise AutofillAbort("CANCELLED")
        if clock() >= deadline:
            raise AutofillAbort("TIMEOUT")
        backend.check(field)

    def wait(predicate, field=None):
        until = min(deadline, clock() + 2)
        while True:
            check(field)
            if predicate():
                return
            if clock() >= until:
                raise AutofillAbort("NAVIGATION_OR_READBACK_FAILED")
            sleep(0.05)

    check()
    backend.preflight()
    check("lpha")
    # Never overwrite an existing text draft, even when it resembles our values.
    if any(backend.read(field) != "" for field in ORDER[1:]):
        raise AutofillAbort("EXISTING_CONTENT")
    for index, field in enumerate(ORDER):
        check(field)
        if field != "lpha" and backend.read(field) != "":
            raise AutofillAbort("EXISTING_CONTENT")
        if field != "lpha" or backend.read(field) != VALUES[field]:
            if field == "lpha":
                backend.prepare_combo(lambda: check(field))
            for character in VALUES[field]:
                check(field)
                backend.character(character)
                sleep(0.06)
            wait(lambda: backend.read(field) == VALUES[field], field)
        if index < len(ORDER) - 1:
            check(field)
            backend.tab()
            next_field = ORDER[index + 1]
            # Focus may be in transit; no input until the exact next field is focused.
            wait(lambda: backend.focused(next_field))
            if backend.read(field) != VALUES[field]:
                raise AutofillAbort("READBACK_CHANGED")
    check("npi")
    if any(backend.read(field) != VALUES[field] for field in ORDER):
        raise AutofillAbort("READBACK_CHANGED")
    return "FILLED_NOT_SUBMITTED"


def normalized_label(value):
    return " ".join(value.strip().rstrip(":").split()).casefold()


def field_matches(control, field, labels):
    role = "ComboBox" if field == "lpha" else "Edit"
    if control.element_info.control_type != role:
        return False
    expected = normalized_label(labels[field])
    names = [control.element_info.name or ""]
    try:
        label = control.element_info.element.CurrentLabeledBy
        if label:
            names.append(label.CurrentName or "")
    except Exception:
        pass
    return bool(expected and any(normalized_label(name) == expected for name in names))


def structure_summary(controls, labels):
    roles = dict.fromkeys(("Edit", "ComboBox", "Pane", "Window", "Document", "Button", "Text", "Other"), 0)
    counts = dict.fromkeys(ORDER, 0)
    errors = 0
    for control in controls:
        try:
            role = control.element_info.control_type
            roles[role if role in roles else "Other"] += 1
            for field in ORDER:
                counts[field] += int(field_matches(control, field, labels))
        except Exception:
            errors += 1
    return {"node_count": len(controls), "roles": roles, "expected_label_matches": counts,
            "metadata_errors": errors}


def unique_controls(controls, labels):
    found = {}
    for field in ORDER:
        candidates = [control for control in controls if field_matches(control, field, labels)]
        if len(candidates) != 1:
            raise AutofillAbort("FIELDS_NOT_UNIQUE_OR_EXPOSED")
        found[field] = candidates[0]
    ids = [tuple(control.element_info.runtime_id or ()) for control in found.values()]
    if any(not identifier for identifier in ids) or len(set(ids)) != len(ids):
        raise AutofillAbort("CONTROL_IDENTITY_UNAVAILABLE")
    return found


class WindowsBackend:
    """Uses UIA only for verification; all mutations are keyboard input."""

    def __init__(self, api, send_key, labels=None):
        import ctypes
        from ctypes import wintypes
        from pywinauto import Desktop
        self.api = api
        self.send_key = send_key
        self.labels = {**LABELS, **(labels or {})}
        self.hwnd = api.GetForegroundWindow()
        self.window = Desktop(backend="uia").window(handle=self.hwnd).wrapper_object()
        self.pid = self.window.process_id()
        import win32api
        import win32process
        self.window_thread, owner = win32process.GetWindowThreadProcessId(self.hwnd)
        if owner != self.pid:
            raise AutofillAbort("WINDOW_OWNER_CHANGED")
        api.GetKeyboardLayout.argtypes = [wintypes.DWORD]
        api.GetKeyboardLayout.restype = wintypes.HANDLE
        api.VkKeyScanExW.argtypes = [wintypes.WCHAR, wintypes.HANDLE]
        api.VkKeyScanExW.restype = ctypes.c_short
        handle = win32api.OpenProcess(0x0410, False, self.pid)
        try:
            require_session_process(win32process.GetModuleFileNameEx(handle, 0))
        finally:
            handle.Close()
        self.controls = {}
        self.ids = {}
        self.layout = self.api.GetKeyboardLayout(0)
        self.mapping = {}

    def escape_down(self):
        return bool(self.api.GetAsyncKeyState(0x1B) & 0x8000)

    def inspect_structure(self):
        # Deliberately independent of preflight/read(): no value or text patterns.
        def check_owner():
            if self.api.GetForegroundWindow() != self.hwnd or self.window.process_id() != self.pid:
                raise AutofillAbort("FOCUS_LOST")
        check_owner()
        controls = self.window.descendants()
        if len(controls) > 2000:
            raise AutofillAbort("TREE_TOO_LARGE")
        summary = structure_summary(controls, self.labels)
        check_owner()
        return summary

    def check(self, field=None):
        if self.api.GetForegroundWindow() != self.hwnd or self.window.process_id() != self.pid:
            raise AutofillAbort("FOCUS_LOST")
        if any(self.api.GetAsyncKeyState(key) & 0x8000 for key in (0x10, 0x11, 0x12, 0x5B, 0x5C)):
            raise AutofillAbort("MODIFIERS_HELD")
        if self.api.GetKeyState(0x14) & 1:
            raise AutofillAbort("CAPS_LOCK_ON")
        if self.api.GetKeyboardLayout(0) != self.layout:
            raise AutofillAbort("KEYBOARD_LAYOUT_CHANGED")
        if self.api.GetKeyboardLayout(self.window_thread) != self.layout:
            raise AutofillAbort("KEYBOARD_LAYOUT_MISMATCH")
        for name, control in self.controls.items():
            if (tuple(control.element_info.runtime_id or ()) != self.ids[name]
                    or control.top_level_parent().handle != self.hwnd
                    or not control.is_visible() or not control.is_enabled()):
                raise AutofillAbort("CONTROL_CHANGED")
        if field is not None and not self.focused(field):
            raise AutofillAbort("UNEXPECTED_FIELD_FOCUS")

    def preflight(self):
        controls = self.window.descendants()
        if len(controls) > 2000:
            raise AutofillAbort("TREE_TOO_LARGE")
        self.controls = unique_controls(controls, self.labels)
        self.ids = {name: tuple(control.element_info.runtime_id) for name, control in self.controls.items()}
        self.check("lpha")
        for field in ORDER:
            control = self.controls[field]
            if control.element_info.element.CurrentIsPassword:
                raise AutofillAbort("UNSAFE_CONTROL")
            if field != "lpha" and not control.is_editable():
                raise AutofillAbort("READ_ONLY_CONTROL")
            self.read(field)
        # Verify every character's keyboard mapping before the first mutation.
        for character in set("".join(VALUES.values())):
            mapped = self.api.VkKeyScanExW(character, self.layout)
            vk = mapped & 0xFF
            if (mapped == -1 or (mapped >> 8) not in (0, 1)
                    or not (0x30 <= vk <= 0x5A or vk == 0x20 or 0xBA <= vk <= 0xE2)):
                raise AutofillAbort("UNSUPPORTED_KEYBOARD_MAPPING")
            self.mapping[character] = (mapped & 0xFF, bool(mapped >> 8))

    def focused(self, field):
        control = self.controls[field]
        if control.has_keyboard_focus():
            return True
        # An editable combo can expose its focusable Edit as a child.
        return field == "lpha" and any(child.has_keyboard_focus() for child in control.children(control_type="Edit"))

    def read(self, field):
        control = self.controls[field]
        value = control.selected_text() if field == "lpha" else control.get_value()
        if not isinstance(value, str):
            raise AutofillAbort("VALUE_UNAVAILABLE")
        return value

    def prepare_combo(self, check):
        check()
        if self.controls["lpha"].is_editable():
            self.send_key(0x11)
            try:
                self.key(0x41)
            finally:
                self.send_key(0x11, key_up=True)
        else:
            self.key(0x24)  # Home, then incremental type-ahead; never Enter.
        check()

    def key(self, vk):
        try:
            self.send_key(vk)
        finally:
            self.send_key(vk, key_up=True)

    def character(self, character):
        vk, shift = self.mapping[character]
        try:
            if shift:
                self.send_key(0x10)
            self.key(vk)
        finally:
            if shift:
                self.send_key(0x10, key_up=True)

    def tab(self):
        self.key(0x09)
