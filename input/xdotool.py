"""Linux input backend: xdotool into an XWayland window.

The window must already have keyboard focus (COSMIC doesn't let X clients take it).
Coordinates are relative to the window, i.e. the same as in captured frames.
"""
from __future__ import annotations

import subprocess
import time


class XdotoolInput:
    def __init__(self, window_id: int, type_delay_ms: int = 80, settle: float = 0.15) -> None:
        self._wid = str(window_id)
        self._type_delay = str(type_delay_ms)
        self._settle = settle  # pause after each action so the UI can react

    def click(self, x: int, y: int) -> None:
        self._run("mousemove", "--window", self._wid, str(x), str(y), "click", "1")

    def type_text(self, text: str) -> None:
        self._run("type", "--delay", self._type_delay, "--", text)

    def key(self, *combos: str) -> None:
        self._run("key", *combos)

    def clear_field(self) -> None:
        self.key("ctrl+a", "BackSpace")

    def _run(self, *args: str) -> None:
        subprocess.run(["xdotool", *args], check=True)
        time.sleep(self._settle)