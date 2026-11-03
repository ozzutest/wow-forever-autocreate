from __future__ import annotations

import ctypes

import numpy as np
import win32gui
from mss import MSS

from .base import WindowNotFoundError


def _enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
    except OSError:
        pass  


class WindowsCapture:
    """Grabs the screen region under the window's client area.

    Unlike X11, this captures the *screen*, so the game must not be covered
    by other windows or minimized.
    """

    def __init__(self, window_name: str) -> None:
        _enable_dpi_awareness()
        self._name = window_name
        self._sct = MSS()
        self._hwnd = 0

    def capture(self) -> np.ndarray:
        shot = self._sct.grab(self._client_rect())
        return np.asarray(shot)[:, :, :3].copy()  # BGRA -> BGR

    def size(self) -> tuple[int, int]:
        r = self._client_rect()
        return r["width"], r["height"]

    def close(self) -> None:
        self._sct.close()

    def _window(self) -> int:
        if not (self._hwnd and win32gui.IsWindow(self._hwnd)):
            self._hwnd = win32gui.FindWindow(None, self._name)
            if not self._hwnd:
                raise WindowNotFoundError(f"window {self._name!r} not found")
        return self._hwnd

    def _client_rect(self) -> dict[str, int]:
        hwnd = self._window()
        if win32gui.IsIconic(hwnd):
            raise WindowNotFoundError(f"window {self._name!r} is minimized")
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        x, y = win32gui.ClientToScreen(hwnd, (0, 0))
        return {"left": x, "top": y, "width": right - left, "height": bottom - top}