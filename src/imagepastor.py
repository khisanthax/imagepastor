from __future__ import annotations

import ctypes
import io
import json
import subprocess
import sys
import threading
import time
import winreg
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageGrab

try:
    import pystray
except ImportError:
    pystray = None

try:
    from pywinauto import Application
except ImportError:
    Application = None


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

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
CAPTURE_HOTKEY_ID = 1
TARGET_HOTKEY_ID = 2
MONITOR_SLEEP_HOTKEY_ID = 3

GA_ROOT = 2
SW_RESTORE = 9
WM_SYSCOMMAND = 0x0112
SC_MONITORPOWER = 0xF170
MONITOR_POWER_ON = -1
MONITOR_POWER_OFF = 2
MONITOR_WINDOW_CLASS_NAME = "ImagePastorMonitorControlWindow"

ES_SYSTEM_REQUIRED = 0x00000001
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


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def log(message: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line, flush=True)
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


paste_target_hwnd: int | None = None
paste_target_composer_title: str | None = None
app_enabled = True
monitor_sleep_guard_enabled = False
monitor_sleep_guard_lock = threading.RLock()
monitor_sleep_guard_cancel_event: threading.Event | None = None
monitor_control_hwnd: int | None = None
monitor_window_hinstance: int | None = None
hotkey_thread_id = 0
tray_icon = None
active_config: dict | None = None
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
    return hwnd


def destroy_monitor_control_window() -> None:
    global monitor_control_hwnd
    global monitor_window_hinstance

    hwnd = monitor_control_hwnd
    hinstance = monitor_window_hinstance
    monitor_control_hwnd = None
    monitor_window_hinstance = None
    if hwnd:
        user32.DestroyWindow(hwnd)
    if hinstance:
        user32.UnregisterClassW(MONITOR_WINDOW_CLASS_NAME, hinstance)


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
        "chatgpt_allowed_hosts": ["chatgpt.com", "chat.openai.com"],
        "auto_click_composer": True,
        "composer_click_offset_from_bottom": 90,
        "restore_mouse_position": True,
        "paste_delay_ms": 250,
        "monitor_sleep_delay_ms": 750,
        "monitor_sleep_guard_interval_seconds": 15,
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
    keys = hotkey_virtual_keys(hotkey)
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


def hovered_root_window() -> int:
    point = get_cursor_pos()
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
    hwnd = hovered_root_window()
    bbox = window_rect(hwnd)
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


def find_chatgpt_composer(window, document_title: str = ""):
    try:
        controls = window.descendants(control_type="Edit")
    except Exception:
        return None

    candidates = []
    for control in controls:
        try:
            if (
                control.element_info.name != "Chat with ChatGPT"
                or not control.is_visible()
                or not control.is_enabled()
            ):
                continue
            candidates.append((control, composer_document_title(control)))
        except Exception:
            continue

    if document_title:
        normalized_target = document_title.casefold()
        for control, title in candidates:
            if title.casefold() == normalized_target:
                return control

    active_title = selected_tab_title(window)
    if active_title:
        normalized_active = active_title.casefold()
        for control, title in candidates:
            if title.casefold() == normalized_active:
                return control

    if len(candidates) == 1:
        return candidates[0][0]
    return None


def paste_target_is_available(hwnd: int, allowed_hosts: list[str]) -> bool:
    window = chromium_window_wrapper(hwnd)
    if window is None:
        return False
    if paste_target_composer_title and find_chatgpt_composer(window, paste_target_composer_title):
        return True
    return chromium_window_has_allowed_address(window, allowed_hosts)


def ensure_chatgpt_tab(hwnd: int, allowed_hosts: list[str], scan_tabs: bool = True) -> bool:
    window = chromium_window_wrapper(hwnd)
    if window is None:
        return False

    if chromium_window_has_allowed_address(window, allowed_hosts):
        return True
    if not scan_tabs:
        return False

    try:
        tabs = window.descendants(control_type="TabItem")
    except Exception as exc:
        log(f"Could not inspect Chromium tabs for '{window_text(hwnd)}': {exc}")
        return False

    original_tab = None
    for tab in tabs:
        try:
            if tab.is_selected():
                original_tab = tab
                break
        except Exception:
            continue

    for tab in tabs:
        try:
            if tab is original_tab:
                continue
            tab.select()
            time.sleep(0.08)
            if chromium_window_has_allowed_address(window, allowed_hosts):
                log(f"Selected a ChatGPT tab in '{window_text(hwnd)}'.")
                return True
        except Exception:
            continue

    if original_tab is not None:
        try:
            original_tab.select()
        except Exception:
            pass
    return False


def find_chatgpt_chromium_tab(allowed_hosts: list[str]) -> int | None:
    chromium_windows = [
        (hwnd, title)
        for hwnd, title in visible_windows()
        if is_valid_chromium_window(hwnd)
    ]

    for hwnd, _title in chromium_windows:
        if ensure_chatgpt_tab(hwnd, allowed_hosts):
            return hwnd

    if chromium_windows:
        titles = "; ".join(title for _hwnd, title in chromium_windows)
        log(f"Inspected Chromium windows but found no ChatGPT URL. Windows: {titles}")
    return None


def find_chatgpt_window(config: dict) -> int | None:
    return find_chatgpt_chromium_tab(config["chatgpt_allowed_hosts"])


def resolve_chatgpt_window(config: dict) -> int | None:
    global paste_target_hwnd
    if paste_target_hwnd:
        if paste_target_is_available(paste_target_hwnd, config["chatgpt_allowed_hosts"]):
            return paste_target_hwnd
        log("Saved paste target is no longer a valid Chromium window with a ChatGPT tab.")
        paste_target_hwnd = None
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
        composer = find_chatgpt_composer(window, paste_target_composer_title) if window else None
        if composer is None and not ensure_chatgpt_tab(
            hwnd, config["chatgpt_allowed_hosts"], scan_tabs=False
        ):
            log("The active browser tab is not ChatGPT; paste cancelled.")
            return False

        if composer is not None:
            rect = composer.rectangle()
            composer_title = composer_document_title(composer) or "selected ChatGPT tile"
            log(f"Focusing ChatGPT composer '{composer_title}' for paste.")
            left_click(
                rect.left + ((rect.right - rect.left) // 2),
                rect.top + ((rect.bottom - rect.top) // 2),
            )
        elif config["auto_click_composer"]:
            left, top, right, bottom = window_rect(hwnd)
            click_x = left + ((right - left) // 2)
            click_y = bottom - int(config["composer_click_offset_from_bottom"])
            left_click(click_x, click_y)
            time.sleep(0.1)

        foreground = user32.GetAncestor(user32.GetForegroundWindow(), GA_ROOT)
        target_still_available = (
            find_chatgpt_composer(window, paste_target_composer_title) is not None
            if composer is not None
            else ensure_chatgpt_tab(hwnd, config["chatgpt_allowed_hosts"], scan_tabs=False)
        )
        if foreground != hwnd or not target_still_available:
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
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        log("Could not set paste target because there is no foreground window.")
        return
    hwnd = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
    if not ensure_chatgpt_tab(hwnd, config["chatgpt_allowed_hosts"], scan_tabs=False):
        log("Paste target was not changed because the active window is not a ChatGPT browser tab.")
        return
    paste_target_hwnd = hwnd
    window = chromium_window_wrapper(hwnd)
    composer = find_chatgpt_composer(window) if window else None
    paste_target_composer_title = composer_document_title(composer) if composer else None
    if paste_target_composer_title:
        log(f"Paste target composer set to '{paste_target_composer_title}'.")
    log(f"Paste target set to '{window_text(hwnd) or f'HWND {hwnd}'}'.")


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


def post_monitor_power(state: int) -> bool:
    hwnd = monitor_control_hwnd
    if not hwnd or not user32.IsWindow(hwnd):
        log("Monitor control window is unavailable.")
        return False
    if not user32.PostMessageW(hwnd, WM_SYSCOMMAND, SC_MONITORPOWER, state):
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


def sleep_monitors(delay_ms: int = 750, log_success: bool = True) -> bool:
    time.sleep(max(0, delay_ms) / 1000)
    if not post_monitor_power(MONITOR_POWER_OFF):
        return False
    if log_success:
        log("Monitor sleep command queued.")
    return True


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


def set_monitor_sleep_guard(enabled: bool, config: dict) -> None:
    global monitor_sleep_guard_enabled
    global monitor_sleep_guard_cancel_event

    worker = None
    with monitor_sleep_guard_lock:
        if monitor_sleep_guard_enabled == enabled:
            return
        monitor_sleep_guard_enabled = enabled
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
            cancel_event = monitor_sleep_guard_cancel_event
            monitor_sleep_guard_cancel_event = None
            if cancel_event is not None:
                cancel_event.set()
            post_monitor_power(MONITOR_POWER_ON)

    log(f"Monitor sleep guard {'enabled' if enabled else 'disabled'}.")
    if worker is not None:
        worker.start()
    update_tray_icon()


def toggle_monitor_sleep_guard(config: dict) -> None:
    with monitor_sleep_guard_lock:
        set_monitor_sleep_guard(not monitor_sleep_guard_enabled, config)


def request_monitor_sleep(config: dict) -> None:
    threading.Thread(
        target=sleep_monitors,
        args=(int(config["monitor_sleep_delay_ms"]),),
        daemon=True,
        name="ImagePastorMonitorSleepOnce",
    ).start()


def tray_sleep_monitors(_icon=None, _item=None) -> None:
    if active_config is not None:
        request_monitor_sleep(active_config)


def tray_toggle_monitor_sleep_guard(_icon=None, _item=None) -> None:
    if active_config is not None:
        toggle_monitor_sleep_guard(active_config)


def toggle_startup(_icon=None, _item=None) -> None:
    try:
        set_startup_enabled(not startup_enabled())
    except OSError as exc:
        log(f"Could not update Windows startup setting: {exc}")
        show_error("ImagePastor", f"Could not update Windows startup setting:\n\n{exc}")
    update_tray_icon()


def tray_exit(icon=None, _item=None) -> None:
    log("Exit requested from tray.")
    if icon:
        icon.stop()
    if hotkey_thread_id:
        user32.PostThreadMessageW(hotkey_thread_id, WM_QUIT, 0, 0)


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
            pystray.MenuItem("Keep Monitors Asleep", tray_toggle_monitor_sleep_guard, checked=lambda _item: monitor_sleep_guard_enabled),
            pystray.MenuItem("Sleep Monitors Once", tray_sleep_monitors),
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

    configure_win32_signatures()
    mutex = kernel32.CreateMutexW(None, False, "ImagePastorSingleInstance")
    if not mutex:
        raise ctypes.WinError()
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        raise RuntimeError("ImagePastor is already running.")

    set_dpi_awareness()
    config = load_config()
    active_config = config
    app_enabled = bool(config["start_enabled"])
    create_monitor_control_window()

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

    msg = MSG()
    try:
        while True:
            message_result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if message_result == -1:
                raise ctypes.WinError()
            if message_result == 0:
                break
            if msg.message == WM_HOTKEY and msg.wParam == CAPTURE_HOTKEY_ID:
                handle_hotkey(config)
            elif msg.message == WM_HOTKEY and msg.wParam == TARGET_HOTKEY_ID:
                set_paste_target(config)
            elif msg.message == WM_HOTKEY and msg.wParam == MONITOR_SLEEP_HOTKEY_ID:
                toggle_monitor_sleep_guard(config)
            else:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
    except KeyboardInterrupt:
        log("Exiting.")
    finally:
        guard_was_enabled = monitor_sleep_guard_enabled
        set_monitor_sleep_guard(False, config)
        if guard_was_enabled and monitor_control_hwnd:
            user32.DefWindowProcW(
                monitor_control_hwnd,
                WM_SYSCOMMAND,
                SC_MONITORPOWER,
                MONITOR_POWER_ON,
            )
        user32.UnregisterHotKey(None, CAPTURE_HOTKEY_ID)
        user32.UnregisterHotKey(None, TARGET_HOTKEY_ID)
        user32.UnregisterHotKey(None, MONITOR_SLEEP_HOTKEY_ID)
        destroy_monitor_control_window()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        log(f"Fatal error: {error}")
        show_error("ImagePastor", f"Fatal error:\n\n{error}")
        raise
