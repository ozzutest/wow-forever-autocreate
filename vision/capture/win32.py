"""Windows capture: the WoW window's client area, grabbed from the screen with mss.

Uses the Windows API through ctypes, so no pywin32 is needed.

WoW must be set to "Fullscreen (Windowed)" (borderless). The pixels come from the
*screen*, so WoW must be in front and not minimized while the bot works.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

import numpy as np
from mss import MSS

from .base import WindowNotFoundError

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]

_dpi_done = False


def make_dpi_aware() -> None:
    """Work in real pixels. Without this, display scaling (125%, 150%) makes the window
    look smaller than it is and every click lands in the wrong place."""
    global _dpi_done
    if _dpi_done:
        return
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)  # per-monitor aware
    except (OSError, AttributeError):
        user32.SetProcessDPIAware()  # older Windows
    _dpi_done = True


def client_rect(hwnd: int) -> tuple[int, int, int, int]:
    """(left, top, width, height) of the window's client area, in screen pixels."""
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise WindowNotFoundError("GetClientRect failed (window closed?)")
    origin = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        raise WindowNotFoundError("ClientToScreen failed (window closed?)")
    return origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top


class Win32Capture:
    def __init__(self, window_name: str) -> None:
        make_dpi_aware()  # before any other window call
        self._name = window_name
        self._sct = MSS()
        self._hwnd: int = 0

    @property
    def window_id(self) -> int:
        return self._window()

    def capture(self) -> np.ndarray:
        left, top, width, height = self._rect()
        shot = self._sct.grab({"left": left, "top": top, "width": width, "height": height})
        return np.asarray(shot)[:, :, :3].copy()  # BGRA -> BGR

    def size(self) -> tuple[int, int]:
        _, _, width, height = self._rect()
        return width, height

    def close(self) -> None:
        self._sct.close()

    def _window(self) -> int:
        # Re-find the window if it was closed or recreated (e.g. after a fullscreen toggle).
        if not (self._hwnd and user32.IsWindow(self._hwnd)):
            hwnd = user32.FindWindowW(None, self._name)
            if not hwnd or not user32.IsWindowVisible(hwnd):
                raise WindowNotFoundError(f"no visible window named {self._name!r}")
            self._hwnd = hwnd
        return self._hwnd

    def _rect(self) -> tuple[int, int, int, int]:
        hwnd = self._window()
        if user32.IsIconic(hwnd):
            raise WindowNotFoundError(f"window {self._name!r} is minimized")
        return client_rect(hwnd)
