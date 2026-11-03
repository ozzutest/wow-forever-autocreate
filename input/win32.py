"""Windows input: mouse clicks and typing with SendInput, through ctypes (no pywin32).
Same interface as XdotoolInput.

Coordinates are relative to the window's client area, i.e. the same as in captured
frames. WoW must have focus (the runner pauses when it doesn't).
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from vision.capture.win32 import client_rect, make_dpi_aware

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0002, 0x0004

# xdotool key names used by the bot -> Windows virtual-key codes
VK = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "BackSpace": 0x08, "Return": 0x0D,
      "Escape": 0x1B, "Tab": 0x09, "Delete": 0x2E}
VK.update({chr(c): c - 32 for c in range(ord("a"), ord("z") + 1)})  # 'a' -> 0x41


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]


def _send(*inputs: INPUT) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    if user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT)) != len(inputs):
        raise OSError(f"SendInput failed (error {ctypes.get_last_error()})")


def _mouse(flags: int) -> INPUT:
    return INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(0, 0, 0, flags, 0, 0))


def _vk(code: int, up: bool) -> INPUT:
    scan = user32.MapVirtualKeyW(code, 0)
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(code, scan, KEYEVENTF_KEYUP if up else 0, 0, 0))


def _unicode(ch: str, up: bool) -> INPUT:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(0, ord(ch), flags, 0, 0))


def active_window_name() -> str | None:
    """Title of the foreground window, or None if there is none."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, len(buf))
    return buf.value or None


class Win32Input:
    def __init__(self, window_id: int, type_delay_ms: int = 80, settle: float = 0.15) -> None:
        make_dpi_aware()
        self._hwnd = window_id
        self._type_delay = type_delay_ms / 1000
        self._settle = settle  # pause after each action so the UI can react

    def click(self, x: int, y: int) -> None:
        left, top, _, _ = client_rect(self._hwnd)
        user32.SetCursorPos(left + x, top + y)
        time.sleep(0.03)  # let the game see the hover before the press
        _send(_mouse(MOUSEEVENTF_LEFTDOWN), _mouse(MOUSEEVENTF_LEFTUP))
        time.sleep(self._settle)

    def type_text(self, text: str) -> None:
        for ch in text:
            _send(_unicode(ch, False), _unicode(ch, True))
            time.sleep(self._type_delay)
        time.sleep(self._settle)

    def key(self, *combos: str) -> None:
        """xdotool-style combos, e.g. key("ctrl+a", "BackSpace")."""
        for combo in combos:
            codes = [VK[k] for k in combo.split("+")]
            _send(*[_vk(c, False) for c in codes], *[_vk(c, True) for c in reversed(codes)])
            time.sleep(0.05)
        time.sleep(self._settle)

    def clear_field(self) -> None:
        self.key("ctrl+a", "BackSpace")