from __future__ import annotations

import os

import numpy as np
import Xlib.error
from Xlib import X, display

from .base import WindowNotFoundError

_STALE_WINDOW_ERRORS = (Xlib.error.BadWindow, Xlib.error.BadDrawable, Xlib.error.BadMatch)


class X11Capture:
    """Captures an X11 window directly by its pixels (works under rootless XWayland)."""

    @property
    def window_id(self) -> int:
       return self._window().id

    def __init__(self, window_name: str) -> None:
        if not os.environ.get("DISPLAY"):
            raise RuntimeError("DISPLAY not set - run from your desktop session terminal")
        self._name = window_name.lower()
        self._d = display.Display()
        self._win = None

    def capture(self) -> np.ndarray:
        try:
            return self._grab(self._window())
        except _STALE_WINDOW_ERRORS:
            self._win = None
            return self._grab(self._window())

    def size(self) -> tuple[int, int]:
        g = self._window().get_geometry()
        return g.width, g.height

    def close(self) -> None:
        self._d.close()

    def _window(self):
        if self._win is None:
            self._win = self._find()
        return self._win

    def _find(self):
        stack = [self._d.screen().root]
        while stack:
            w = stack.pop()
            try:
                stack.extend(w.query_tree().children)
                if (w.get_wm_name() or "").lower() != self._name:
                    continue
                if w.get_attributes().map_state != X.IsViewable:
                    continue
                g = w.get_geometry()
                if g.width > 1 and g.height > 1:
                    return w
            except Xlib.error.BadWindow:  # window vanished mid-walk
                continue
        raise WindowNotFoundError(f"window {self._name!r} not found")

    @staticmethod
    def _grab(win) -> np.ndarray:
        g = win.get_geometry()
        raw = win.get_image(0, 0, g.width, g.height, X.ZPixmap, 0xFFFFFFFF)
        expected = g.width * g.height * 4
        if len(raw.data) != expected:
            raise RuntimeError(f"unexpected image size {len(raw.data)}, expected {expected}")
        frame = np.frombuffer(raw.data, np.uint8).reshape(g.height, g.width, 4)
        return frame[:, :, :3].copy()  # BGRX -> BGR