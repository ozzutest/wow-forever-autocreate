"""Pick the input backend for this OS. Both have the same interface:
Input(window_id).click(x, y) / type_text(s) / key(*combos) / clear_field(),
and active_window_name() -> title of the focused window, or None."""
import sys

if sys.platform == "win32":
    from input.win32 import Win32Input as Input, active_window_name
else:
    from input.xdotool import XdotoolInput as Input, active_window_name

__all__ = ["Input", "active_window_name"]