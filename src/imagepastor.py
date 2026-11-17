from __future__ import annotations

import ctypes
import io
import json
import os
import subprocess
import sys
import threading
import tempfile
import time
import winreg
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageGrab
from face_wake import FaceWatcher, worker_main
from runtime_diagnostics import RuntimeDiagnostics
from away_sleep import AwayGate
from lpha_autofill import AutofillAbort, WindowsBackend, fill as fill_lpha

try:
    import pystray
except ImportError:
    pystray = None

try:
    from pywinauto import Application, Desktop
except ImportError:
    Application = None
    Desktop = None


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
wtsapi32 = ctypes.windll.wtsapi32

try:
    shcore = ctypes.windll.shcore
except AttributeError:
    shcore = None


MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_TIMER = 0x0113
WM_POWERBROADCAST = 0x0218
WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
PBT_POWERSETTINGCHANGE = 0x8013
WM_MONITOR_POWER = 0x8001
CAPTURE_HOTKEY_ID = 1
TARGET_HOTKEY_ID = 2
MONITOR_SLEEP_HOTKEY_ID = 3
LPHA_HOTKEY_ID = 4
LPHA_INSPECT_HOTKEY_ID = 5
lpha_cancel = threading.Event()
lpha_lock = threading.Lock()

GA_ROOT = 2
SW_RESTORE = 9
WM_SYSCOMMAND = 0x0112
SC_MONITORPOWER = 0xF170
MONITOR_POWER_ON = -1
MONITOR_POWER_OFF = 2
MONITOR_WINDOW_CLASS_NAME = "ImagePastorMonitorControlWindow"
MONITOR_WAKE_TIMER_ID = 1
MONITOR_WAKE_GRACE_SECONDS = 10.0
SESSION_DISPLAY_GUID = (ctypes.c_ubyte * 16).from_buffer_copy(
    bytes.fromhex("0ec2842b23addf4d93db05ffbd7efca5")
)

ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
ES_CONTINUOUS = 0x80000000

CF_DIB = 8
GMEM_MOVEABLE = 0x0002
MB_ICONERROR = 0x00000010
ERROR_ALREADY_EXISTS = 183

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_V = 0x56
diagnostics = None


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def log(message: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    try:
        print(line, flush=True)
    except (OSError, ValueError):
        pass
    try:
        with (app_dir() / "imagepastor.log").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def show_error(title: str, message: str) -> None:
    try:
        user32.MessageBoxW(None, message, title, MB_ICONERROR)
    except OSError:
        pass


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUTUNION)]


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
WNDPROC = ctypes.WINFUNCTYPE(
    wintypes.LPARAM,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class POWERBROADCAST_SETTING(ctypes.Structure):
    _fields_ = [
        ("PowerSetting", ctypes.c_ubyte * 16),
        ("DataLength", wintypes.DWORD),
        ("Data", ctypes.c_ubyte * 1),
    ]


paste_target_hwnd: int | None = None
paste_target_composer_title: str | None = None
paste_target_composer_url: str | None = None
paste_target_document_id: tuple | None = None
app_enabled = True
monitor_sleep_guard_enabled = False
monitor_sleep_guard_lock = threading.RLock()
monitor_sleep_guard_cancel_event: threading.Event | None = None
monitor_sleep_once_cancel_event: threading.Event | None = None
monitor_power_generation = 0
monitor_power_desired = MONITOR_POWER_ON
monitor_power_pending: tuple[int, int] | None = None
monitor_display_state: int | None = None
monitor_wake_until = 0.0
monitor_control_hwnd: int | None = None
monitor_window_hinstance: int | None = None
monitor_notification_handle: int | None = None
monitor_session_notifications = False
monitor_control_thread: threading.Thread | None = None
monitor_control_thread_id = 0
hotkey_thread_id = 0
tray_icon = None
active_config: dict | None = None
face_watcher = FaceWatcher()
face_wake_cancel: threading.Event | None = None
face_wake_session_locked = False
auto_sleep_cancel: threading.Event | None = None
app_shutting_down = False
STARTUP_APP_NAME = "ImagePastor"
STARTUP_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
CHROME_APP_PATHS_KEY = r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
VIVALDI_APP_PATHS_KEY = r"Software\Microsoft\Windows\CurrentVersion\App Paths\vivaldi.exe"


def chime_extension_dir() -> Path:
    folder_name = "ImagePastor-Chime" if getattr(sys, "frozen", False) else "chrome-extension"
    return app_dir() / folder_name


def browser_executable() -> str:
    browsers = (
        (
            VIVALDI_APP_PATHS_KEY,
            (
                Path.home() / "AppData/Local/Vivaldi/Application/vivaldi.exe",
                Path("C:/Program Files/Vivaldi/Application/vivaldi.exe"),
                Path("C:/Program Files (x86)/Vivaldi/Application/vivaldi.exe"),
            ),
            "vivaldi.exe",
        ),
        (
            CHROME_APP_PATHS_KEY,
            (
                Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe",
                Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
                Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
            ),
            "chrome.exe",
        ),
    )
    for app_paths_key, candidates, fallback in browsers:
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, app_paths_key, 0, winreg.KEY_READ) as key:
                    executable, _value_type = winreg.QueryValueEx(key, "")
                if executable and Path(executable).is_file():
                    return executable
            except (FileNotFoundError, OSError):
                continue
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        if fallback == "chrome.exe":
            return fallback
    return "chrome.exe"


def open_chime_extension_setup(_icon=None, _item=None) -> None:
    extension_dir = chime_extension_dir()
    if not (extension_dir / "manifest.json").is_file():
        message = f"The ChatGPT chime extension was not found at:\n\n{extension_dir}"
        log(message.replace("\n", " "))
        show_error("ImagePastor", message)
        return

    try:
        subprocess.Popen(["explorer.exe", str(extension_dir)])
        subprocess.Popen([browser_executable(), "chrome://extensions/"])
        log(f"Opened Chromium extension setup and '{extension_dir}'.")
    except OSError as exc:
        log(f"Could not open ChatGPT chime setup: {exc}")
        show_error("ImagePastor", f"Could not open ChatGPT chime setup:\n\n{exc}")


@WNDPROC
def monitor_control_window_proc(hwnd, message, wparam, lparam):
    global face_wake_session_locked
    if message == WM_WTSSESSION_CHANGE:
        if wparam in (WTS_SESSION_LOCK, WTS_SESSION_UNLOCK):
            label = "unlocked" if wparam == WTS_SESSION_UNLOCK else "locked"
            log(f"Windows session {label}.")
        if wparam == WTS_SESSION_LOCK:
            with monitor_sleep_guard_lock:
                face_wake_session_locked = True
                stop_face_wake()
                stop_auto_sleep()
        if wparam == WTS_SESSION_UNLOCK:
            face_wake_session_locked = False
            wake_monitors_after_unlock()
            start_auto_sleep(active_config or {})
        return 0
    if message == WM_MONITOR_POWER:
        apply_monitor_power(hwnd, int(wparam), int(lparam))
        return 0
    if message == WM_TIMER and wparam == MONITOR_WAKE_TIMER_ID:
        finish_monitor_wake(hwnd)
        return 0
    if message == WM_POWERBROADCAST and wparam == PBT_POWERSETTINGCHANGE and lparam:
        setting = ctypes.cast(lparam, ctypes.POINTER(POWERBROADCAST_SETTING)).contents
        if bytes(setting.PowerSetting) == bytes(SESSION_DISPLAY_GUID) and setting.DataLength == 4:
            state = wintypes.DWORD.from_address(lparam + POWERBROADCAST_SETTING.Data.offset).value
            record_monitor_display_state(state)
        return 1
    return user32.DefWindowProcW(hwnd, message, wparam, lparam)


def configure_win32_signatures() -> None:
    user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.WindowFromPoint.argtypes = [POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetCursorPos.restype = wintypes.BOOL
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = wintypes.SHORT
    user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    user32.DefWindowProcW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.DefWindowProcW.restype = wintypes.LPARAM
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    user32.UnregisterClassW.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
    user32.DispatchMessageW.restype = wintypes.LPARAM
    user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, wintypes.LPVOID]
    user32.SetTimer.restype = ctypes.c_size_t
    user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
    user32.KillTimer.restype = wintypes.BOOL
    user32.RegisterPowerSettingNotification.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD]
    user32.RegisterPowerSettingNotification.restype = wintypes.HANDLE
    user32.UnregisterPowerSettingNotification.argtypes = [wintypes.HANDLE]
    user32.UnregisterPowerSettingNotification.restype = wintypes.BOOL
    wtsapi32.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
    wtsapi32.WTSRegisterSessionNotification.restype = wintypes.BOOL
    wtsapi32.WTSUnRegisterSessionNotification.argtypes = [wintypes.HWND]
    wtsapi32.WTSUnRegisterSessionNotification.restype = wintypes.BOOL
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.GetLastError.argtypes = []
    kernel32.GetLastError.restype = wintypes.DWORD
    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.SetThreadExecutionState.argtypes = [wintypes.DWORD]
    kernel32.SetThreadExecutionState.restype = wintypes.DWORD


def create_monitor_control_window() -> int:
    global monitor_control_hwnd
    global monitor_window_hinstance
    global monitor_notification_handle
    global monitor_session_notifications

    hinstance = kernel32.GetModuleHandleW(None)
    if not hinstance:
        raise ctypes.WinError()

    window_class = WNDCLASSW()
    window_class.lpfnWndProc = monitor_control_window_proc
    window_class.hInstance = hinstance
    window_class.lpszClassName = MONITOR_WINDOW_CLASS_NAME
    if not user32.RegisterClassW(ctypes.byref(window_class)):
        raise ctypes.WinError()

    hwnd = user32.CreateWindowExW(
        0,
        MONITOR_WINDOW_CLASS_NAME,
        "",
        0,
        0,
        0,
        0,
        0,
        None,
        None,
        hinstance,
        None,
    )
    if not hwnd:
        user32.UnregisterClassW(MONITOR_WINDOW_CLASS_NAME, hinstance)
        raise ctypes.WinError()

    monitor_control_hwnd = hwnd
    monitor_window_hinstance = hinstance
    monitor_notification_handle = user32.RegisterPowerSettingNotification(
        hwnd, ctypes.byref(SESSION_DISPLAY_GUID), 0
    )
    if not monitor_notification_handle:
        log("Display state notifications unavailable; monitor guard will use periodic checks.")
    monitor_session_notifications = bool(wtsapi32.WTSRegisterSessionNotification(hwnd, 0))
    if not monitor_session_notifications:
        log("Session notifications unavailable; use the monitor hotkey after unlocking Windows.")
    return hwnd


def destroy_monitor_control_window() -> None:
    global monitor_control_hwnd
    global monitor_window_hinstance
    global monitor_notification_handle
    global monitor_session_notifications

    hwnd = monitor_control_hwnd
    hinstance = monitor_window_hinstance
    monitor_control_hwnd = None
    monitor_window_hinstance = None
    if monitor_notification_handle:
        user32.UnregisterPowerSettingNotification(monitor_notification_handle)
        monitor_notification_handle = None
    if monitor_session_notifications and hwnd:
        wtsapi32.WTSUnRegisterSessionNotification(hwnd)
        monitor_session_notifications = False
    if hwnd:
        user32.DestroyWindow(hwnd)
    if hinstance:
        user32.UnregisterClassW(MONITOR_WINDOW_CLASS_NAME, hinstance)


def run_monitor_control(ready: threading.Event, errors: list[Exception]) -> None:
    global monitor_control_thread_id
    try:
        monitor_control_thread_id = kernel32.GetCurrentThreadId()
        create_monitor_control_window()
        ready.set()
        msg = MSG()
        while True:
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result == -1:
                raise ctypes.WinError()
            if result == 0:
                return
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    except Exception as exc:
        errors.append(exc)
        log(f"Monitor control error: {exc}")
    finally:
        ready.set()
        kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        destroy_monitor_control_window()
        monitor_control_thread_id = 0


def start_monitor_control_thread() -> None:
    global monitor_control_thread
    ready = threading.Event()
    errors: list[Exception] = []
    monitor_control_thread = threading.Thread(
        target=run_monitor_control, args=(ready, errors), daemon=True,
        name="ImagePastorMonitorControl",
    )
    monitor_control_thread.start()
    if not ready.wait(5.0):
        raise RuntimeError("Monitor control thread did not start within five seconds.")
    if errors:
        raise RuntimeError(f"Could not start monitor control: {errors[0]}")
    log(f"Monitor power controller started on thread {monitor_control_thread_id}.")


def stop_monitor_control_thread() -> None:
    if monitor_control_thread_id:
        user32.PostThreadMessageW(monitor_control_thread_id, WM_QUIT, 0, 0)
    if monitor_control_thread:
        monitor_control_thread.join(timeout=3.0)


def set_dpi_awareness() -> None:
    if shcore:
        try:
            shcore.SetProcessDpiAwareness(2)
            return
        except OSError:
            pass
    try:
        user32.SetProcessDPIAware()
    except OSError:
        pass


def load_config() -> dict:
    base = {
        "hotkey": {"modifiers": ["ctrl", "alt"], "key": "v"},
        "target_hotkey": {"modifiers": ["ctrl", "alt", "shift"], "key": "v"},
        "monitor_sleep_hotkey": {"modifiers": ["ctrl", "alt", "shift"], "key": "m"},
        "lpha_hotkey": {"modifiers": ["ctrl", "alt"], "key": "l"},
        "lpha_inspect_hotkey": {"modifiers": ["ctrl", "alt", "shift"], "key": "l"},
        "lpha_show_menu": True,
        "lpha_labels": {},
        "chatgpt_allowed_hosts": ["chatgpt.com", "chat.openai.com"],
        "auto_click_composer": True,
        "composer_click_offset_from_bottom": 90,
        "restore_mouse_position": True,
        "capture_vivaldi_tile": True,
        "paste_delay_ms": 250,
        "monitor_sleep_delay_ms": 750,
        "monitor_sleep_guard_interval_seconds": 15,
        "face_wake_enabled": False,
        "auto_sleep_enabled": False,
        "face_wake_camera_index": 0,
        "save_captures": False,
        "capture_directory": "captures",
        "start_enabled": True,
    }
    path = app_dir() / "config.json"
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            user_config = json.load(handle)
        base.update(user_config)
        if "hotkey" in user_config:
            base["hotkey"] = {**{"modifiers": ["ctrl", "alt"], "key": "v"}, **user_config["hotkey"]}
        if "target_hotkey" in user_config:
            base["target_hotkey"] = {
                **{"modifiers": ["ctrl", "alt", "shift"], "key": "v"},
                **user_config["target_hotkey"],
            }
        if "monitor_sleep_hotkey" in user_config:
            base["monitor_sleep_hotkey"] = {
                **{"modifiers": ["ctrl", "alt", "shift"], "key": "m"},
                **user_config["monitor_sleep_hotkey"],
            }
        base["lpha_hotkey"] = {**{"modifiers": ["ctrl", "alt"], "key": "l"},
                               **user_config.get("lpha_hotkey", {})}
        base["lpha_inspect_hotkey"] = {**{"modifiers": ["ctrl", "alt", "shift"], "key": "l"},
                                       **user_config.get("lpha_inspect_hotkey", {})}
    return base


def startup_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'
    return f'"{Path(sys.executable).resolve()}" "{Path(__file__).resolve()}"'


def startup_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _value_type = winreg.QueryValueEx(key, STARTUP_APP_NAME)
    except FileNotFoundError:
        return False
    return value == startup_command()


def set_startup_enabled(enabled: bool) -> None:
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        STARTUP_RUN_KEY,
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        if enabled:
            winreg.SetValueEx(key, STARTUP_APP_NAME, 0, winreg.REG_SZ, startup_command())
            log("Windows startup enabled.")
            return
        try:
            winreg.DeleteValue(key, STARTUP_APP_NAME)
        except FileNotFoundError:
            pass
        log("Windows startup disabled.")


def hotkey_modifiers(names: list[str]) -> int:
    value = MOD_NOREPEAT
    for name in names:
        normalized = name.lower()
        if normalized in {"ctrl", "control"}:
            value |= MOD_CONTROL
        elif normalized == "alt":
            value |= MOD_ALT
        elif normalized == "shift":
            value |= MOD_SHIFT
        elif normalized in {"win", "windows"}:
            value |= MOD_WIN
        else:
            raise ValueError(f"Unsupported modifier: {name}")
    return value


def virtual_key(key: str) -> int:
    if len(key) == 1:
        return ord(key.upper())
    function_keys = {f"f{i}": 0x70 + i - 1 for i in range(1, 25)}
    normalized = key.lower()
    if normalized in function_keys:
        return function_keys[normalized]
    raise ValueError(f"Unsupported hotkey key: {key}")


def hotkey_virtual_keys(hotkey: dict) -> list[int]:
    modifier_keys = {
        "ctrl": [VK_CONTROL],
        "control": [VK_CONTROL],
        "alt": [VK_MENU],
        "shift": [VK_SHIFT],
        "win": [VK_LWIN, VK_RWIN],
        "windows": [VK_LWIN, VK_RWIN],
    }
    keys = [virtual_key(hotkey["key"])]
    for modifier in hotkey["modifiers"]:
        keys.extend(modifier_keys[modifier.lower()])
    return keys


def wait_for_hotkey_release(hotkey: dict, timeout_seconds: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    keys = set(hotkey_virtual_keys(hotkey)) | {VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN}
    while time.monotonic() < deadline:
        if not any(user32.GetAsyncKeyState(key) & 0x8000 for key in keys):
            return True
        time.sleep(0.015)
    return False


def get_cursor_pos() -> POINT:
    point = POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        raise ctypes.WinError()
    return point


def window_text(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def hovered_root_window(point=None) -> int:
    point = point if point is not None else get_cursor_pos()
    hwnd = user32.WindowFromPoint(point)
    if not hwnd:
        raise RuntimeError("No window found under cursor")
    root = user32.GetAncestor(hwnd, GA_ROOT)
    return root or hwnd


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError()
    if rect.right <= rect.left or rect.bottom <= rect.top:
        raise RuntimeError("Hovered window has an empty rectangle")
    return rect.left, rect.top, rect.right, rect.bottom


def capture_window_under_cursor(config: dict):
    point = get_cursor_pos()
    hwnd = hovered_root_window(point)
    bbox = window_rect(hwnd)
    if (config.get("capture_vivaldi_tile", True) and is_valid_chromium_window(hwnd)
            and window_text(hwnd).casefold().endswith(" - vivaldi")):
        bbox = vivaldi_tile_rect(point, bbox)
        log(f"Capturing Vivaldi tile region: {bbox[2] - bbox[0]}x{bbox[3] - bbox[1]}.")
    image = ImageGrab.grab(bbox=bbox, all_screens=True)
    title = window_text(hwnd) or f"HWND {hwnd}"

    if config["save_captures"]:
        capture_dir = app_dir() / config["capture_directory"]
        capture_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        image.save(capture_dir / f"{stamp}.png")

    return image, title


def image_to_clipboard(image) -> None:
    output = io.BytesIO()
    image.convert("RGB").save(output, "BMP")
    data = output.getvalue()[14:]
    output.close()

    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    clipboard_open = False
    try:
        if not handle:
            raise ctypes.WinError()
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            raise ctypes.WinError()
        ctypes.memmove(pointer, data, len(data))
        kernel32.GlobalUnlock(handle)

        for _attempt in range(10):
            if user32.OpenClipboard(None):
                clipboard_open = True
                break
            time.sleep(0.025)
        if not clipboard_open:
            raise RuntimeError("Windows clipboard remained busy")
        if not user32.EmptyClipboard():
            raise ctypes.WinError()
        if not user32.SetClipboardData(CF_DIB, handle):
            raise ctypes.WinError()
        handle = None
    finally:
        if clipboard_open:
            user32.CloseClipboard()
        if handle:
            kernel32.GlobalFree(handle)


def visible_windows() -> list[tuple[int, str]]:
    windows: list[tuple[int, str]] = []

    def enum_proc(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            title = window_text(hwnd)
            if title:
                windows.append((hwnd, title))
        return True

    user32.EnumWindows(WNDENUMPROC(enum_proc), 0)
    return windows


def window_class(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    if not user32.GetClassNameW(hwnd, buffer, len(buffer)):
        return ""
    return buffer.value


def is_valid_chromium_window(hwnd: int) -> bool:
    return bool(
        hwnd
        and user32.IsWindow(hwnd)
        and user32.IsWindowVisible(hwnd)
        and window_class(hwnd) == "Chrome_WidgetWin_1"
    )


def url_host(url: str) -> str:
    candidate = url.strip()
    if not candidate:
        return ""
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    return (urlparse(candidate).hostname or "").lower().rstrip(".")


def host_is_allowed(host: str, allowed_hosts: list[str]) -> bool:
    normalized = host.lower().rstrip(".")
    for allowed in allowed_hosts:
        allowed = allowed.strip().lower().rstrip(".")
        if allowed and (normalized == allowed or normalized.endswith(f".{allowed}")):
            return True
    return False


def chromium_window_wrapper(hwnd: int):
    if Application is None:
        log("pywinauto is unavailable; cannot inspect Chromium tabs.")
        return None
    if not is_valid_chromium_window(hwnd):
        return None
    try:
        app = Application(backend="uia").connect(handle=hwnd, timeout=1)
        return app.window(handle=hwnd)
    except Exception as exc:
        log(f"Could not inspect Chromium window '{window_text(hwnd)}': {exc}")
        return None


def chromium_address_bar_values(window) -> list[str]:
    try:
        edit_controls = window.descendants(control_type="Edit")
    except Exception:
        return []
    values = []
    for control in edit_controls:
        try:
            name = control.element_info.name.lower()
            class_name = control.element_info.class_name.lower()
            if not any(hint in f"{name} {class_name}" for hint in ("address", "omnibox", "url")):
                continue
            value = control.get_value().strip()
            if value and value not in values:
                values.append(value)
        except Exception:
            continue
    return values


def chromium_window_has_allowed_address(window, allowed_hosts: list[str]) -> bool:
    return any(
        host_is_allowed(url_host(value), allowed_hosts)
        for value in chromium_address_bar_values(window)
    )


def selected_tab_title(window) -> str:
    try:
        tabs = window.descendants(control_type="TabItem")
    except Exception:
        return ""
    for tab in tabs:
        try:
            if tab.is_selected():
                return (tab.element_info.name or "").strip()
        except Exception:
            continue
    return ""


def composer_document_title(control) -> str:
    ancestor = control
    for _depth in range(10):
        try:
            info = ancestor.element_info
            if info.control_type == "Document" and info.name:
                return info.name.strip()
            ancestor = ancestor.parent()
        except Exception:
            return ""
    return ""


def document_value_urls(document):
    for reader in (lambda: document.iface_value.CurrentValue,
                   lambda: document.get_value(),
                   lambda: document.legacy_properties().get("Value", "")):
        try:
            value = reader()
            if isinstance(value, str) and value:
                yield value
        except Exception:
            continue


def vivaldi_tile_rect(point, window_bounds):
    if Desktop is None:
        raise RuntimeError("Cannot identify the Vivaldi tile; capture cancelled.")
    ancestor = Desktop(backend="uia").from_point(point.x, point.y)
    page = None
    for _depth in range(64):
        try:
            if ancestor.element_info.control_type == "Document" and any(
                    urlparse(value).scheme.lower() in {"http", "https", "file"}
                    for value in document_value_urls(ancestor)):
                page = ancestor  # Outermost web page, not a nested iframe or Vivaldi's chrome-extension UI.
            ancestor = ancestor.parent()
            if ancestor is None:
                break
        except Exception:
            raise RuntimeError("Could not establish the Vivaldi page ancestry; capture cancelled.") from None
    if page is None:
        raise RuntimeError("Point inside the web page of the desired Vivaldi tile; capture cancelled.")
    rect = page.rectangle()
    left, top, right, bottom = window_bounds
    bounds = max(left, rect.left), max(top, rect.top), min(right, rect.right), min(bottom, rect.bottom)
    if not (bounds[0] <= point.x < bounds[2] and bounds[1] <= point.y < bounds[3]):
        raise RuntimeError("Vivaldi tile bounds are unavailable; capture cancelled.")
    return bounds


def chatgpt_composer_document(control, allowed_hosts):
    ancestor = control
    for _depth in range(16):
        try:
            if ancestor.element_info.control_type == "Document":
                for value in document_value_urls(ancestor):
                    if host_is_allowed(url_host(value), allowed_hosts):
                        return ancestor, value
        except Exception:
            pass
        try:
            ancestor = ancestor.parent()
        except Exception:
            break
    return None, ""


def document_runtime_id(document):
    try:
        value = document.element_info.runtime_id
        if value and isinstance(value, (tuple, list)) and all(type(item) is int for item in value):
            return tuple(value)
    except Exception:
        pass
    return None


def find_chatgpt_composer(window, document_title: str = "", document_url: str = "",
                          point=None, allowed_hosts=None, document_id=None, diagnostics=None):
    if allowed_hosts is None:
        allowed_hosts = ["chatgpt.com", "chat.openai.com"]
    try:
        controls = window.descendants(control_type="Edit")
    except Exception:
        return None

    candidates = []
    recognized = 0
    for control in controls:
        try:
            if (
                control.element_info.name not in {"Chat with ChatGPT", "Ask ChatGPT"}
                or not control.is_visible()
                or not control.is_enabled()
            ):
                continue
            recognized += 1
            document, url = chatgpt_composer_document(control, allowed_hosts)
            if document is not None:
                candidates.append((control, document.element_info.name.strip(), url, document))
        except Exception:
            continue
    if diagnostics is not None:
        diagnostics.update(edit_controls=len(controls), recognized_editors=recognized, verified_editors=len(candidates))

    if document_title or document_url or document_id:
        exact = [control for control, title, url, document in candidates
                 if (not document_title or title.casefold() == document_title.casefold())
                 and (not document_url or url == document_url)
                 and (not document_id or document_runtime_id(document) == document_id)]
        return exact[0] if len(exact) == 1 else None

    if point is not None:
        hovered = []
        for control, _title, _url, document in candidates:
            try:
                rect = document.rectangle()
                if rect.left <= point.x < rect.right and rect.top <= point.y < rect.bottom:
                    hovered.append(control)
            except Exception:
                continue
        return hovered[0] if len(hovered) == 1 else None

    active_title = selected_tab_title(window)
    if active_title:
        normalized_active = active_title.casefold()
        active = [control for control, title, _url, _document in candidates if title.casefold() == normalized_active]
        if len(active) == 1:
            return active[0]

    if len(candidates) == 1:
        return candidates[0][0]
    return None


def composer_under_pointer(window, point, allowed_hosts, diagnostics):
    if Desktop is not None:
        try:
            hit = Desktop(backend="uia").from_point(point.x, point.y)
            document, url = chatgpt_composer_document(hit, allowed_hosts)
            diagnostics["pointer_document_verified"] = document is not None
            if document is not None:
                return find_chatgpt_composer(window, document_url=url, allowed_hosts=allowed_hosts,
                                             document_id=document_runtime_id(document), diagnostics=diagnostics)
        except Exception:
            diagnostics["pointer_hit_test_failed"] = True
    return find_chatgpt_composer(window, point=point, allowed_hosts=allowed_hosts, diagnostics=diagnostics)


def paste_target_is_available(hwnd: int, allowed_hosts: list[str]) -> bool:
    window = chromium_window_wrapper(hwnd)
    if window is None:
        return False
    if paste_target_composer_title or paste_target_composer_url:
        return find_chatgpt_composer(window, paste_target_composer_title, paste_target_composer_url,
                                     allowed_hosts=allowed_hosts, document_id=paste_target_document_id) is not None
    return chromium_window_has_allowed_address(window, allowed_hosts)


def ensure_chatgpt_tab(hwnd: int, allowed_hosts: list[str], scan_tabs: bool = False) -> bool:
    window = chromium_window_wrapper(hwnd)
    if window is None:
        return False

    # scan_tabs is retained for call compatibility, but target lookup never selects tabs.
    return chromium_window_has_allowed_address(window, allowed_hosts)


def find_chatgpt_chromium_tab(allowed_hosts: list[str]) -> int | None:
    chromium_windows = [
        (hwnd, title)
        for hwnd, title in visible_windows()
        if is_valid_chromium_window(hwnd)
    ]

    matches = [hwnd for hwnd, _title in chromium_windows if ensure_chatgpt_tab(hwnd, allowed_hosts, scan_tabs=False)]
    if len(matches) == 1:
        return matches[0]
    log("No unique currently-open ChatGPT window; select a tile with the target hotkey. No tabs were changed.")
    return None


def find_chatgpt_window(config: dict) -> int | None:
    return find_chatgpt_chromium_tab(config["chatgpt_allowed_hosts"])


def resolve_chatgpt_window(config: dict) -> int | None:
    global paste_target_hwnd
    if paste_target_hwnd:
        if paste_target_is_available(paste_target_hwnd, config["chatgpt_allowed_hosts"]):
            return paste_target_hwnd
        log("Saved ChatGPT tile is unavailable or ambiguous; reselect it with the target hotkey. Paste cancelled.")
        return None
    return find_chatgpt_window(config)


def send_key(vk: int, key_up: bool = False) -> None:
    flags = KEYEVENTF_KEYUP if key_up else 0
    item = INPUT(type=INPUT_KEYBOARD, union=INPUTUNION(ki=KEYBDINPUT(vk, 0, flags, 0, None)))
    sent = user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT))
    if sent != 1:
        raise ctypes.WinError()


def send_ctrl_v() -> None:
    send_key(VK_CONTROL)
    v_is_down = False
    try:
        send_key(VK_V)
        v_is_down = True
        send_key(VK_V, key_up=True)
        v_is_down = False
    finally:
        if v_is_down:
            try:
                send_key(VK_V, key_up=True)
            except OSError:
                pass
        send_key(VK_CONTROL, key_up=True)


def left_click(x: int, y: int) -> None:
    if not user32.SetCursorPos(x, y):
        raise ctypes.WinError()
    down = INPUT(
        type=INPUT_MOUSE,
        union=INPUTUNION(mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, None)),
    )
    up = INPUT(
        type=INPUT_MOUSE,
        union=INPUTUNION(mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, None)),
    )
    events = (INPUT * 2)(down, up)
    sent = user32.SendInput(2, events, ctypes.sizeof(INPUT))
    if sent != 2:
        raise ctypes.WinError()


def input_modifiers_down() -> bool:
    return any(user32.GetAsyncKeyState(key) & 0x8000
               for key in (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN))


def paste_into_chatgpt(config: dict) -> bool:
    hwnd = resolve_chatgpt_window(config)
    if not hwnd:
        return False

    old_pos = get_cursor_pos()
    try:
        user32.ShowWindow(hwnd, SW_RESTORE)
        focus_requested = bool(user32.SetForegroundWindow(hwnd))
        time.sleep(config["paste_delay_ms"] / 1000)

        foreground = user32.GetAncestor(user32.GetForegroundWindow(), GA_ROOT)
        if foreground != hwnd:
            reason = "Windows refused the focus request" if not focus_requested else "Focus moved elsewhere"
            log(f"{reason}; paste cancelled.")
            return False
        window = chromium_window_wrapper(hwnd)
        composer = find_chatgpt_composer(window, paste_target_composer_title, paste_target_composer_url,
                                         allowed_hosts=config["chatgpt_allowed_hosts"],
                                         document_id=paste_target_document_id) if window else None
        if composer is None:
            log("No unique verified ChatGPT composer; paste cancelled. Select the desired tile again.")
            return False

        document, document_url = chatgpt_composer_document(composer, config["chatgpt_allowed_hosts"])
        if document is None:
            return False
        document_title = document.element_info.name.strip()
        document_id = document_runtime_id(document)
        rect = composer.rectangle()
        if rect.right <= rect.left or rect.bottom <= rect.top:
            return False
        if input_modifiers_down():
            log("Modifier key still held; automatic click/paste cancelled.")
            return False
        log("Focusing the verified ChatGPT tile composer for paste.")
        left_click(rect.left + ((rect.right - rect.left) // 2), rect.top + ((rect.bottom - rect.top) // 2))
        time.sleep(0.1)

        foreground = user32.GetAncestor(user32.GetForegroundWindow(), GA_ROOT)
        current = find_chatgpt_composer(window, document_title, document_url,
                                       allowed_hosts=config["chatgpt_allowed_hosts"], document_id=document_id)
        target_still_available = current is not None and current.has_keyboard_focus()
        if foreground != hwnd or not target_still_available or input_modifiers_down():
            log("ChatGPT lost focus or changed tabs before paste; paste cancelled.")
            return False

        send_ctrl_v()
        return True
    finally:
        if config["restore_mouse_position"]:
            user32.SetCursorPos(old_pos.x, old_pos.y)


def set_paste_target(config: dict) -> None:
    global paste_target_hwnd
    global paste_target_composer_title
    global paste_target_composer_url
    global paste_target_document_id
    point = get_cursor_pos()
    hovered = user32.GetAncestor(user32.WindowFromPoint(point), GA_ROOT)
    use_pointer = is_valid_chromium_window(hovered)
    hwnd = hovered if use_pointer else user32.GetForegroundWindow()
    if not hwnd:
        log("Could not set paste target because there is no foreground window.")
        return
    hwnd = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
    window = chromium_window_wrapper(hwnd)
    diagnostics = {}
    composer = (composer_under_pointer(window, point, config["chatgpt_allowed_hosts"], diagnostics)
                if use_pointer else find_chatgpt_composer(window, allowed_hosts=config["chatgpt_allowed_hosts"],
                                                         diagnostics=diagnostics)) if window else None
    if composer is None:
        log(f"Paste target unchanged: no unique verified ChatGPT editor. Target diagnostics: {diagnostics}")
        return
    document, url = chatgpt_composer_document(composer, config["chatgpt_allowed_hosts"])
    if document is None:
        log("Paste target unchanged: ChatGPT document URL could not be verified.")
        return
    try:
        title = document.element_info.name.strip()
    except Exception:
        log("Paste target unchanged: the ChatGPT document changed during selection.")
        return
    paste_target_hwnd = hwnd
    paste_target_composer_title = title
    paste_target_composer_url = url
    paste_target_document_id = document_runtime_id(document)
    log("Paste target set to the verified ChatGPT tile. No text or image was pasted.")


def create_tray_image(enabled: bool) -> Image.Image:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    fill = (31, 148, 92, 255) if enabled else (138, 138, 138, 255)
    outline = (15, 82, 50, 255) if enabled else (72, 72, 72, 255)
    draw.rounded_rectangle((8, 8, 56, 56), radius=12, fill=fill, outline=outline, width=3)
    draw.rectangle((19, 18, 45, 24), fill=(255, 255, 255, 255))
    draw.rectangle((19, 30, 45, 36), fill=(255, 255, 255, 255))
    draw.rectangle((19, 42, 36, 48), fill=(255, 255, 255, 255))
    return image


def update_tray_icon() -> None:
    if tray_icon:
        tray_icon.icon = create_tray_image(app_enabled)
        tray_icon.title = f"ImagePastor ({'On' if app_enabled else 'Off'})"
        tray_icon.update_menu()


def toggle_enabled(_icon=None, _item=None) -> None:
    global app_enabled
    app_enabled = not app_enabled
    log(f"ImagePastor {'enabled' if app_enabled else 'disabled'} from tray.")
    update_tray_icon()


def tray_set_target(_icon=None, _item=None) -> None:
    if active_config is not None:
        set_paste_target(active_config)


def record_monitor_display_state(state: int) -> None:
    global monitor_display_state
    with monitor_sleep_guard_lock:
        monitor_display_state = state
    label = {0: "off", 1: "on", 2: "dimmed"}.get(state, str(state))
    log(f"Windows reports session display {label}.")


def apply_monitor_power(hwnd: int, generation: int, state: int) -> None:
    global monitor_power_pending
    global monitor_wake_until
    with monitor_sleep_guard_lock:
        if monitor_power_pending == (generation, state):
            monitor_power_pending = None
        if generation != monitor_power_generation or state != monitor_power_desired:
            log(f"Discarded cancelled monitor command: state={state}, generation={generation}.")
            return
        if state == MONITOR_POWER_OFF and monitor_display_state == 0:
            return
        if state == MONITOR_POWER_ON:
            monitor_wake_until = max(
                monitor_wake_until, time.monotonic() + MONITOR_WAKE_GRACE_SECONDS
            )

    # Driver calls must never hold the state lock or block the hotkey thread.
    if state == MONITOR_POWER_ON:
        flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
        if not kernel32.SetThreadExecutionState(flags):
            log("Could not request temporary display-awake protection.")
        if not user32.SetTimer(hwnd, MONITOR_WAKE_TIMER_ID, 1000, None):
            kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            log("Could not schedule release of display-awake protection.")
    else:
        user32.KillTimer(hwnd, MONITOR_WAKE_TIMER_ID)
        kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    started = time.monotonic()
    label = "on" if state == MONITOR_POWER_ON else "off"
    log(f"Applying monitor power {label}, generation={generation}.")
    user32.DefWindowProcW(hwnd, WM_SYSCOMMAND, SC_MONITORPOWER, state)
    log(f"Monitor power {label} call returned in {time.monotonic() - started:.3f}s.")


def finish_monitor_wake(hwnd: int) -> None:
    with monitor_sleep_guard_lock:
        keep_waking = time.monotonic() < monitor_wake_until
        display_on = monitor_display_state == 1
    if keep_waking:
        if not display_on:
            kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
        return
    user32.KillTimer(hwnd, MONITOR_WAKE_TIMER_ID)
    kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    log("Monitor wake protection finished; normal Windows power policy restored.")


def post_monitor_power(state: int, generation: int | None = None) -> bool:
    global monitor_power_pending
    with monitor_sleep_guard_lock:
        if generation is None:
            generation = monitor_power_generation
        if generation != monitor_power_generation or state != monitor_power_desired:
            return False
        command = (generation, state)
        if monitor_power_pending == command:
            return True
        hwnd = monitor_control_hwnd
        if not hwnd or not user32.IsWindow(hwnd):
            log("Monitor control window is unavailable.")
            return False
        monitor_power_pending = command
        if not user32.PostMessageW(hwnd, WM_MONITOR_POWER, generation, state):
            monitor_power_pending = None
            log(f"Could not queue monitor power state {state}.")
            return False
        return True


def post_guard_monitor_off(cancel_event: threading.Event) -> bool:
    with monitor_sleep_guard_lock:
        if (
            cancel_event.is_set()
            or not monitor_sleep_guard_enabled
            or monitor_sleep_guard_cancel_event is not cancel_event
        ):
            return False
        return post_monitor_power(MONITOR_POWER_OFF)


def sleep_monitors(delay_ms: int, cancel_event: threading.Event, generation: int) -> bool:
    if cancel_event.wait(max(0, delay_ms) / 1000):
        return False
    if post_monitor_power(MONITOR_POWER_OFF, generation):
        log("Monitor sleep command queued.")
        return True
    return False


def monitor_sleep_guard(config: dict, cancel_event: threading.Event) -> None:
    keep_awake_set = bool(
        kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    )
    if keep_awake_set:
        log("Windows system-sleep prevention enabled while monitor guard is active.")
    else:
        log("Could not request Windows system-sleep prevention.")

    try:
        initial_delay = max(0, int(config["monitor_sleep_delay_ms"])) / 1000
        if cancel_event.wait(initial_delay):
            return

        interval = max(1, int(config["monitor_sleep_guard_interval_seconds"]))
        while not cancel_event.is_set():
            if not post_guard_monitor_off(cancel_event):
                return
            if cancel_event.wait(interval):
                return
    finally:
        if keep_awake_set:
            kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            log("Windows system-sleep prevention released.")


def set_monitor_sleep_guard(enabled: bool, config: dict, *, already_away=False) -> None:
    global monitor_sleep_guard_enabled
    global monitor_sleep_guard_cancel_event
    global monitor_sleep_once_cancel_event
    global monitor_power_generation
    global monitor_power_desired
    global monitor_wake_until

    worker = None
    with monitor_sleep_guard_lock:
        if enabled and monitor_sleep_guard_enabled:
            return
        monitor_power_generation += 1
        monitor_power_desired = MONITOR_POWER_OFF if enabled else MONITOR_POWER_ON
        if monitor_sleep_once_cancel_event is not None:
            monitor_sleep_once_cancel_event.set()
            monitor_sleep_once_cancel_event = None
        monitor_sleep_guard_enabled = enabled
        stop_auto_sleep()
        if enabled:
            cancel_event = threading.Event()
            monitor_sleep_guard_cancel_event = cancel_event
            worker = threading.Thread(
                target=monitor_sleep_guard,
                args=(config, cancel_event),
                daemon=True,
                name="ImagePastorMonitorGuard",
            )
        else:
            stop_face_wake()
            monitor_wake_until = time.monotonic() + MONITOR_WAKE_GRACE_SECONDS
            cancel_event = monitor_sleep_guard_cancel_event
            monitor_sleep_guard_cancel_event = None
            if cancel_event is not None:
                cancel_event.set()
            post_monitor_power(MONITOR_POWER_ON)

    log(f"Monitor sleep guard {'enabled' if enabled else 'disabled'}.")
    if not enabled:
        # Reset idle timers immediately, even if a previous driver call has not returned.
        if not kernel32.SetThreadExecutionState(ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED):
            log("Could not request immediate monitor wake.")
    if worker is not None:
        worker.start()
        # Auto-sleep has already established departure; manual sleep has not.
        wake_config = {**config, "_face_wake_armed": True} if already_away else config
        start_face_wake(wake_config)
    elif not app_shutting_down:
        start_auto_sleep(config)
    update_tray_icon()


def last_input_idle_seconds():
    class LastInput(ctypes.Structure):
        _fields_ = [("size", wintypes.UINT), ("tick", wintypes.DWORD)]
    info = LastInput(ctypes.sizeof(LastInput), 0)
    user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LastInput)]
    user32.GetLastInputInfo.restype = wintypes.BOOL
    kernel32.GetTickCount.restype = wintypes.DWORD
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    elapsed = (kernel32.GetTickCount() - info.tick) & 0xFFFFFFFF
    return elapsed / 1000 if elapsed < 0x80000000 else None


def desktop_is_unlocked():
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    handle = user32.OpenInputDesktop(0, False, 1)
    if not handle:
        return False
    try:
        name = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        return bool(user32.GetUserObjectInformationW(handle, 2, name, ctypes.sizeof(name), ctypes.byref(needed))
                    and name.value.casefold() == "default")
    finally:
        user32.CloseDesktop(handle)


def stop_auto_sleep():
    global auto_sleep_cancel
    if auto_sleep_cancel is not None:
        auto_sleep_cancel.set()
        auto_sleep_cancel = None


def start_auto_sleep(config):
    global auto_sleep_cancel
    with monitor_sleep_guard_lock:
        stop_auto_sleep()
        if (not config.get("auto_sleep_enabled", False) or monitor_sleep_guard_enabled
                or face_wake_session_locked or app_shutting_down or not desktop_is_unlocked()):
            return
        cancel = threading.Event()
        auto_sleep_cancel = cancel
        gate = AwayGate()

        def observe(status):
            with monitor_sleep_guard_lock:
                if (cancel.is_set() or auto_sleep_cancel is not cancel or monitor_sleep_guard_enabled
                        or face_wake_session_locked or app_shutting_down):
                    return
                if not desktop_is_unlocked() or lpha_lock.locked():
                    gate.reset()
                    return
                if gate.observe(status, time.monotonic(), last_input_idle_seconds()):
                    log("15 minutes without a face or keyboard/mouse activity; enabling monitor sleep guard.")
                    set_monitor_sleep_guard(True, config, already_away=True)

        threading.Thread(target=face_watcher.run, args=(cancel, dict(config), None, log),
                         kwargs={"observe": observe}, daemon=False, name="ImagePastorAutoSleep").start()


def toggle_auto_sleep(_icon=None, _item=None):
    if active_config is None:
        return
    temporary = None
    try:
        enabled = not active_config.get("auto_sleep_enabled", False)
        path = app_dir() / "config.json"
        saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        saved["auto_sleep_enabled"] = enabled
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False, suffix=".tmp") as handle:
            temporary = Path(handle.name)
            json.dump(saved, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        active_config["auto_sleep_enabled"] = enabled
        with monitor_sleep_guard_lock:
            if enabled:
                start_auto_sleep(active_config)
            else:
                stop_auto_sleep()
        log(f"15-minute away auto-sleep {'enabled' if enabled else 'disabled'}.")
        update_tray_icon()
    except (OSError, ValueError, TypeError) as exc:
        log(f"Could not save auto-sleep setting ({type(exc).__name__}).")
        show_error("ImagePastor", "Could not save the auto-sleep setting.")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def stop_face_wake() -> None:
    global face_wake_cancel
    with monitor_sleep_guard_lock:
        if face_wake_cancel is not None:
            face_wake_cancel.set()
            face_wake_cancel = None


def start_face_wake(config: dict) -> None:
    global face_wake_cancel
    with monitor_sleep_guard_lock:
        stop_face_wake()
        if (not config.get("face_wake_enabled", False) or not monitor_sleep_guard_enabled
                or face_wake_session_locked):
            return
        guard = monitor_sleep_guard_cancel_event
        cancel = threading.Event()
        face_wake_cancel = cancel

        def wake():
            with monitor_sleep_guard_lock:
                if (not cancel.is_set() and face_wake_cancel is cancel and monitor_sleep_guard_enabled
                        and monitor_sleep_guard_cancel_event is guard):
                    log("Sustained face detected; disabling monitor sleep guard and waking displays.")
                    set_monitor_sleep_guard(False, config)

        threading.Thread(target=face_watcher.run, args=(cancel, dict(config), wake, log),
                         daemon=False, name="ImagePastorFaceWake").start()


def toggle_face_wake(_icon=None, _item=None) -> None:
    if active_config is None:
        return
    enabled = not active_config.get("face_wake_enabled", False)
    temporary = None
    try:
        path = app_dir() / "config.json"
        saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        saved["face_wake_enabled"] = enabled
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False, suffix=".tmp") as handle:
            temporary = Path(handle.name)
            json.dump(saved, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        active_config["face_wake_enabled"] = enabled
        if enabled:
            start_face_wake(active_config)
        else:
            stop_face_wake()
        log(f"Face wake {'enabled' if enabled else 'disabled'}.")
        update_tray_icon()
    except (OSError, ValueError, TypeError) as exc:
        log(f"Could not save face wake setting ({type(exc).__name__}).")
        show_error("ImagePastor", "Could not save the face wake setting.")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def toggle_monitor_sleep_guard(config: dict) -> None:
    with monitor_sleep_guard_lock:
        if not monitor_sleep_guard_enabled and time.monotonic() < monitor_wake_until:
            log("Monitor hotkey repeated during wake; keeping monitors awake.")
            return
        wants_sleep = not monitor_sleep_guard_enabled and monitor_power_desired != MONITOR_POWER_OFF
        set_monitor_sleep_guard(wants_sleep, config)


def request_monitor_sleep(config: dict) -> None:
    global monitor_sleep_once_cancel_event
    global monitor_power_generation
    global monitor_power_desired
    with monitor_sleep_guard_lock:
        if time.monotonic() < monitor_wake_until:
            log("Monitor sleep request ignored while monitors are waking.")
            return
        if monitor_sleep_once_cancel_event is not None:
            monitor_sleep_once_cancel_event.set()
        cancel_event = threading.Event()
        monitor_sleep_once_cancel_event = cancel_event
        monitor_power_generation += 1
        generation = monitor_power_generation
        monitor_power_desired = MONITOR_POWER_OFF
    threading.Thread(
        target=sleep_monitors,
        args=(int(config["monitor_sleep_delay_ms"]), cancel_event, generation),
        daemon=True,
        name="ImagePastorMonitorSleepOnce",
    ).start()


def tray_sleep_monitors(_icon=None, _item=None) -> None:
    if active_config is not None:
        request_monitor_sleep(active_config)


def tray_toggle_monitor_sleep_guard(_icon=None, _item=None) -> None:
    if active_config is not None:
        toggle_monitor_sleep_guard(active_config)


def tray_wake_monitors(_icon=None, _item=None) -> None:
    if active_config is not None:
        set_monitor_sleep_guard(False, active_config)


def wake_monitors_after_unlock() -> None:
    with monitor_sleep_guard_lock:
        if monitor_sleep_guard_enabled or monitor_power_desired == MONITOR_POWER_OFF:
            log("Unlocking Windows cancels monitor sleep and requests wake.")
            set_monitor_sleep_guard(False, active_config or {})


def toggle_startup(_icon=None, _item=None) -> None:
    try:
        set_startup_enabled(not startup_enabled())
    except OSError as exc:
        log(f"Could not update Windows startup setting: {exc}")
        show_error("ImagePastor", f"Could not update Windows startup setting:\n\n{exc}")
    update_tray_icon()


def tray_exit(icon=None, _item=None) -> None:
    lpha_cancel.set()
    log("Exit requested from tray.")
    if icon:
        icon.stop()
    if hotkey_thread_id:
        user32.PostThreadMessageW(hotkey_thread_id, WM_QUIT, 0, 0)


def start_lpha_autofill(_icon=None, _item=None, *, from_hotkey=False, inspect_only=False) -> None:
    if active_config is not None and not active_config.get("lpha_enabled", True):
        notify_lpha("LPHA Autofill is disabled in this installation's configuration.")
        return
    if not lpha_lock.acquire(blocking=False):
        lpha_cancel.set()
        log("LPHA Autofill: cancellation requested; any partial entries are preserved.")
        return
    lpha_cancel.clear()

    def worker():
        initialized = False
        try:
            import pythoncom
            pythoncom.CoInitialize()
            initialized = True
            if not from_hotkey and lpha_cancel.wait(3):
                raise AutofillAbort("CANCELLED")
            hotkey_name = "lpha_inspect_hotkey" if inspect_only else "lpha_hotkey"
            if not active_config or not wait_for_hotkey_release(active_config[hotkey_name]):
                raise AutofillAbort("HOTKEY_HELD")
            if monitor_sleep_guard_enabled:
                raise AutofillAbort("MONITOR_GUARD_ACTIVE")
            backend = WindowsBackend(user32, send_key, active_config.get("lpha_labels"))
            if inspect_only:
                snapshots = []
                for _ in range(2):
                    if lpha_cancel.is_set():
                        raise AutofillAbort("CANCELLED")
                    snapshots.append(backend.inspect_structure())
                    if lpha_cancel.wait(0.25):
                        raise AutofillAbort("CANCELLED")
                log("LPHA read-only structure: " + json.dumps({"snapshots": snapshots}, sort_keys=True))
                notify_lpha("Read-only field check complete. Counts saved in the log; no values read or input sent.")
                return
            result = fill_lpha(backend, lambda: lpha_cancel.is_set() or monitor_sleep_guard_enabled)
            log(f"LPHA Autofill: {result}. Review manually; no Save, Send, or Sign was used.")
            notify_lpha("Fields filled. Review manually; nothing was submitted or signed.")
        except AutofillAbort as exc:
            log(f"LPHA Autofill stopped: {exc.code}. Any partial entries are preserved; review manually.")
            notify_lpha(f"Stopped: {exc.code}. Review any partial entries manually.")
        except Exception:
            # UIA exceptions can include patient names/values. Never log them.
            log("LPHA Autofill stopped: ACCESSIBILITY_OR_INPUT_UNAVAILABLE. Review manually.")
            notify_lpha("Stopped: accessibility or input unavailable. Review manually.")
        finally:
            if initialized:
                pythoncom.CoUninitialize()
            lpha_lock.release()

    log("LPHA read-only check requested; no input will be sent." if inspect_only else
        "LPHA Autofill requested. Escape or the hotkey again cancels; no automatic cleanup.")
    threading.Thread(target=worker, name="lpha-autofill", daemon=True).start()


def cancel_lpha_autofill(_icon=None, _item=None) -> None:
    lpha_cancel.set()


def notify_lpha(message: str) -> None:
    if tray_icon:
        try:
            tray_icon.notify(message, "LPHA Autofill")
        except Exception:
            pass


def register_lpha_hotkey(config: dict, *, inspect_only=False) -> bool:
    if not config.get("lpha_enabled", True):
        return False
    try:
        def signature(name):
            hotkey = config[name]
            return hotkey_modifiers(hotkey["modifiers"]), virtual_key(hotkey["key"])
        name = "lpha_inspect_hotkey" if inspect_only else "lpha_hotkey"
        identifier = LPHA_INSPECT_HOTKEY_ID if inspect_only else LPHA_HOTKEY_ID
        desired = signature(name)
        others = ["hotkey", "target_hotkey", "monitor_sleep_hotkey"]
        if inspect_only:
            others.append("lpha_hotkey")
        if desired in {signature(other) for other in others}:
            log("LPHA hotkey conflicts with an ImagePastor shortcut; LPHA hotkey disabled.")
            return False
        if user32.RegisterHotKey(None, identifier, *desired):
            log("LPHA read-only hotkey registered." if inspect_only else "LPHA Autofill global hotkey registered.")
            return True
    except (KeyError, TypeError, ValueError, AttributeError):
        pass
    log("LPHA hotkey unavailable; existing shortcuts are unchanged. Use the tray menu or configure another combo.")
    return False


def run_tray() -> None:
    global tray_icon
    if pystray is None:
        log("pystray is unavailable; running without tray icon.")
        return

    tray_icon = pystray.Icon(
        "ImagePastor",
        create_tray_image(app_enabled),
        f"ImagePastor ({'On' if app_enabled else 'Off'})",
        menu=pystray.Menu(
            pystray.MenuItem("Enabled", toggle_enabled, checked=lambda _item: app_enabled),
            pystray.MenuItem("Set Current Window as Target", tray_set_target),
            pystray.MenuItem("LPHA Autofill (3-second delay)", start_lpha_autofill,
                             visible=lambda _item: bool(active_config and active_config.get("lpha_enabled", True)
                                                        and active_config.get("lpha_show_menu", True))),
            pystray.MenuItem("Cancel LPHA Autofill", cancel_lpha_autofill,
                             enabled=lambda _item: lpha_lock.locked()),
            pystray.MenuItem("Keep Monitors Asleep", tray_toggle_monitor_sleep_guard, checked=lambda _item: monitor_sleep_guard_enabled),
            pystray.MenuItem("Sleep Monitors Once", tray_sleep_monitors),
            pystray.MenuItem("Wake Monitors", tray_wake_monitors),
            pystray.MenuItem("Wake on Face", toggle_face_wake,
                             checked=lambda _item: bool(active_config and active_config.get("face_wake_enabled", False))),
            pystray.MenuItem("Auto Sleep After 15 Min Away", toggle_auto_sleep,
                             checked=lambda _item: bool(active_config and active_config.get("auto_sleep_enabled", False))),
            pystray.MenuItem("Set Up ChatGPT Chime", open_chime_extension_setup),
            pystray.MenuItem("Start with Windows", toggle_startup, checked=lambda _item: startup_enabled()),
            pystray.MenuItem("Exit", tray_exit),
        ),
    )
    tray_icon.run()


def handle_hotkey(config: dict) -> None:
    if not app_enabled:
        log("Capture hotkey ignored because ImagePastor is disabled.")
        return
    try:
        image, title = capture_window_under_cursor(config)
        image_to_clipboard(image)
        if not wait_for_hotkey_release(config["hotkey"]):
            log("Capture hotkey was still held; automatic paste cancelled. The image remains on the clipboard.")
            return
        pasted = paste_into_chatgpt(config)
        target = "pasted into ChatGPT" if pasted else "copied to clipboard; automatic paste did not complete"
        log(f"Captured '{title}' and {target}.")
    except Exception as exc:
        log(f"Error: {exc}")


def main() -> int:
    global app_enabled
    global hotkey_thread_id
    global active_config
    global diagnostics
    global app_shutting_down

    configure_win32_signatures()
    mutex = kernel32.CreateMutexW(None, False, "ImagePastorSingleInstance")
    if not mutex:
        raise ctypes.WinError()
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        raise RuntimeError("ImagePastor is already running.")

    diagnostics = RuntimeDiagnostics(app_dir(), log)
    try:
        diagnostics.start()
    except OSError as exc:
        log(f"Process diagnostics unavailable: {type(exc).__name__}.")

    set_dpi_awareness()
    config = load_config()
    active_config = config
    app_enabled = bool(config["start_enabled"])
    start_monitor_control_thread()

    capture_modifiers = hotkey_modifiers(config["hotkey"]["modifiers"])
    capture_vk = virtual_key(config["hotkey"]["key"])
    target_modifiers = hotkey_modifiers(config["target_hotkey"]["modifiers"])
    target_vk = virtual_key(config["target_hotkey"]["key"])
    monitor_modifiers = hotkey_modifiers(config["monitor_sleep_hotkey"]["modifiers"])
    monitor_vk = virtual_key(config["monitor_sleep_hotkey"]["key"])
    if not user32.RegisterHotKey(None, CAPTURE_HOTKEY_ID, capture_modifiers, capture_vk):
        combo = "+".join([*config["hotkey"]["modifiers"], config["hotkey"]["key"]])
        raise RuntimeError(f"Could not register hotkey {combo}. Another app is already using it.")
    if not user32.RegisterHotKey(None, TARGET_HOTKEY_ID, target_modifiers, target_vk):
        combo = "+".join([*config["target_hotkey"]["modifiers"], config["target_hotkey"]["key"]])
        user32.UnregisterHotKey(None, CAPTURE_HOTKEY_ID)
        raise RuntimeError(f"Could not register target hotkey {combo}. Another app is already using it.")
    if not user32.RegisterHotKey(None, MONITOR_SLEEP_HOTKEY_ID, monitor_modifiers, monitor_vk):
        combo = "+".join([*config["monitor_sleep_hotkey"]["modifiers"], config["monitor_sleep_hotkey"]["key"]])
        user32.UnregisterHotKey(None, TARGET_HOTKEY_ID)
        user32.UnregisterHotKey(None, CAPTURE_HOTKEY_ID)
        raise RuntimeError(f"Could not register monitor sleep hotkey {combo}. Another app is already using it.")

    lpha_available = register_lpha_hotkey(config)
    lpha_inspect_available = register_lpha_hotkey(config, inspect_only=True)

    combo = "+".join([*config["hotkey"]["modifiers"], config["hotkey"]["key"]])
    target_combo = "+".join([*config["target_hotkey"]["modifiers"], config["target_hotkey"]["key"]])
    monitor_combo = "+".join(
        [*config["monitor_sleep_hotkey"]["modifiers"], config["monitor_sleep_hotkey"]["key"]]
    )
    hotkey_thread_id = kernel32.GetCurrentThreadId()
    log(
        "ImagePastor running. "
        f"Capture hotkey: {combo}. Target hotkey: {target_combo}. "
        f"Monitor sleep guard hotkey: {monitor_combo}."
    )
    threading.Thread(target=run_tray, daemon=True).start()
    start_auto_sleep(config)

    msg = MSG()
    try:
        while True:
            message_result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if message_result == -1:
                raise ctypes.WinError()
            if message_result == 0:
                log("Hotkey message loop received WM_QUIT.")
                break
            if msg.message == WM_HOTKEY and msg.wParam == CAPTURE_HOTKEY_ID:
                if lpha_lock.locked():
                    cancel_lpha_autofill()
                    log("Capture skipped while LPHA Autofill cancels; retry after it stops.")
                else:
                    handle_hotkey(config)
            elif msg.message == WM_HOTKEY and msg.wParam == TARGET_HOTKEY_ID:
                if lpha_lock.locked():
                    cancel_lpha_autofill()
                else:
                    set_paste_target(config)
            elif msg.message == WM_HOTKEY and msg.wParam == MONITOR_SLEEP_HOTKEY_ID:
                cancel_lpha_autofill()
                toggle_monitor_sleep_guard(config)
            elif msg.message == WM_HOTKEY and msg.wParam == LPHA_HOTKEY_ID:
                start_lpha_autofill(from_hotkey=True)
            elif msg.message == WM_HOTKEY and msg.wParam == LPHA_INSPECT_HOTKEY_ID:
                start_lpha_autofill(from_hotkey=True, inspect_only=True)
            else:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
    except KeyboardInterrupt:
        log("Exiting.")
    finally:
        app_shutting_down = True
        stop_auto_sleep()
        cancel_lpha_autofill()
        set_monitor_sleep_guard(False, config)
        user32.UnregisterHotKey(None, CAPTURE_HOTKEY_ID)
        user32.UnregisterHotKey(None, TARGET_HOTKEY_ID)
        user32.UnregisterHotKey(None, MONITOR_SLEEP_HOTKEY_ID)
        if lpha_available:
            user32.UnregisterHotKey(None, LPHA_HOTKEY_ID)
        if lpha_inspect_available:
            user32.UnregisterHotKey(None, LPHA_INSPECT_HOTKEY_ID)
        stop_monitor_control_thread()
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--face-wake-worker":
        raise SystemExit(worker_main())
    if "--app-worker" not in sys.argv:
        from process_supervisor import supervise
        configure_win32_signatures()
        supervisor_mutex = kernel32.CreateMutexW(None, False, "ImagePastorSupervisor")
        if not supervisor_mutex:
            raise ctypes.WinError()
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            raise SystemExit(0)
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command.append(str(Path(__file__).resolve()))
        command.append("--app-worker")
        raise SystemExit(supervise(command, app_dir()))
    exit_status = "error"
    try:
        result = main()
        exit_status = "clean"
        raise SystemExit(result)
    except Exception as error:
        if diagnostics:
            diagnostics.report_exception("Fatal main exception", type(error), error.__traceback__)
        log(f"Fatal error: {error}")
        show_error("ImagePastor", f"Fatal error:\n\n{error}")
        raise
    finally:
        if diagnostics:
            try:
                diagnostics.finish(exit_status)
            except OSError:
                log("Could not persist process exit receipt.")
