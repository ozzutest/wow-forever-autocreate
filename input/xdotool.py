"""Linux input backend: xdotool into an XWayland window.

The window must already have keyboard focus (COSMIC doesn't let X clients take it).
Coordinates are relative to the window, i.e. the same as in captured frames.
"""
from __future__ import annotations

import subprocess
import time


def active_window_name() -> str | None:
    """Title of the focused X window, or None if xdotool can't tell (e.g. no X window focused)."""
    try:
        out = subprocess.run(["xdotool", "getactivewindow", "getwindowname"],
                             capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


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