from __future__ import annotations

import sys
from typing import Protocol

import numpy as np


class WindowNotFoundError(LookupError):
    """The game window does not exist (yet) or is not visible."""


class WindowCapture(Protocol):
    def capture(self) -> np.ndarray:
        """Return the window's client area as a BGR uint8 array of shape (H, W, 3)."""
        ...

    def size(self) -> tuple[int, int]:
        """Return (width, height) of the window's client area."""
        ...

    def close(self) -> None: ...


def make_capture(window_name: str) -> WindowCapture:
    if sys.platform.startswith("linux"):
        from .x11 import X11Capture
        return X11Capture(window_name)
    if sys.platform == "win32":
        from .windows import WindowsCapture
        return WindowsCapture(window_name)
    raise RuntimeError(f"unsupported platform: {sys.platform}")



