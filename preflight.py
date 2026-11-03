"""Before-run check: is everything the bot needs working on this machine, right now?

Usage (from the project root, with WoW running fullscreen):
    python -m preflight
    python -m preflight --config characters.yaml

It never clicks or types. The only input it sends is a mouse *move* over the top edge
of the WoW window, to prove the input tool can reach the window. Works on Linux and Windows.
Exit code 0 = ready (warnings allowed), 1 = something must be fixed first.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WINDOW_NAME = "World of Warcraft"
RECOGNIZE_FOR = 10.0   # s to keep looking for a known screen (loading screens and fades)
results: list[tuple[str, str, str]] = []  # (status, check, detail)


def report(status: str, check: str, detail: str = "") -> None:
    results.append((status, check, detail))
    mark = {"OK": "\033[32m OK \033[0m", "WARN": "\033[33mWARN\033[0m", "FAIL": "\033[31mFAIL\033[0m"}[status]
    print(f"[{mark}] {check}" + (f": {detail}" if detail else ""))


def check_python() -> bool:
    ok = sys.version_info >= (3, 11)
    report("OK" if ok else "FAIL", "Python version", platform.python_version()
           + ("" if ok else " (need 3.11 or newer)"))
    return ok


def check_imports() -> bool:
    good = True
    mods = ["numpy", "cv2", "pytesseract", "yaml", "pydantic"]
    if sys.platform.startswith("linux"):
        mods.append("Xlib")
    elif sys.platform == "win32":
        mods.append("mss")
    for mod in mods:
        try:
            __import__(mod)
        except ImportError as e:
            report("FAIL", f"Python package {mod}", f"{e} (run: pip install -r requirements.txt)")
            good = False
    if good:
        report("OK", "Python packages", ", ".join(mods))
    return good


def check_tools() -> bool:
    good = True
    try:
        import pytesseract
        import vision.ocr  # noqa: F401  (on Windows this finds Tesseract in Program Files)
        report("OK", "Tesseract OCR", str(pytesseract.get_tesseract_version()))
    except Exception as e:
        how = ("install it from https://github.com/UB-Mannheim/tesseract/wiki (default folder)"
               if sys.platform == "win32" else "sudo apt install tesseract-ocr")
        report("FAIL", "Tesseract OCR", f"{e} ({how})")
        good = False
    if sys.platform.startswith("linux"):
        if shutil.which("xdotool"):
            report("OK", "xdotool", shutil.which("xdotool"))
        else:
            report("FAIL", "xdotool", "not found (install: sudo apt install xdotool)")
            good = False
        if os.environ.get("DISPLAY"):
            report("OK", "DISPLAY", os.environ["DISPLAY"])
        else:
            report("FAIL", "DISPLAY", "not set: run this from a terminal on the desktop, not over SSH")
            good = False
    return good


def check_config(config: Path):
    """Returns (game, cfg, titles, target) or None."""
    try:
        from config import load_characters, load_game
        from config.models import ModelType
        game = load_game(ROOT / "config" / "races.yaml")
        cfg = load_characters(config, game)
    except FileNotFoundError as e:
        report("FAIL", "Characters file", f"{e} (copy characters.example.yaml to characters.yaml)")
        return None
    except Exception as e:
        report("FAIL", "Characters file", f"{config.name} is invalid:\n{e}")
        return None
    good = True
    for i, ch in enumerate(cfg.characters):
        model = ModelType.HD if ch.hd_model else ModelType.SD
        g = getattr(ch.gender, "value", ch.gender)
        looks = game.races[ch.race].appearance.get(model, {}).get(ch.gender)
        names = ", ".join(f"{p.main} {p.secondary}" for p in ch.names)
        if looks is None:
            report("FAIL", f"Character #{i + 1}", f"no {model.value} {g} {ch.race} data in races.yaml")
            good = False
        else:
            report("OK", f"Character #{i + 1}",
                   f"{g} {ch.race} {getattr(ch.char_class, 'value', ch.char_class)}, names: {names}")
    rulesets = game.rulesets
    title = lambda t: t if isinstance(t, str) else t.title
    target = title(rulesets[getattr(cfg.ruleset, "value", cfg.ruleset)])
    report("OK", "Ruleset", target)
    return (game, cfg, [title(t) for t in rulesets.values()], target) if good else None


def check_window():
    import yaml
    layout = yaml.safe_load((ROOT / "vision" / "layout.yaml").read_text(encoding="utf-8"))
    want = tuple(layout["window_size"])
    try:
        from vision.capture import WindowNotFoundError, make_capture
        cap = make_capture(WINDOW_NAME)
    except ImportError as e:
        # A missing or outdated bot file, or a missing package: not a WoW problem.
        report("FAIL", "Capture backend", f"failed to load ({e}). Update the bot files and "
               "run: pip install -r requirements.txt")
        return None
    except Exception as e:
        report("FAIL", "Capture backend", f"failed to start: {e}")
        return None
    try:
        size = cap.size()  # the first call is where the window is actually looked up
    except WindowNotFoundError as e:
        report("FAIL", "WoW window", f"{e}. Is WoW running, in Fullscreen (Windowed), not minimized?")
        cap.close()
        return None
    except Exception as e:
        report("FAIL", "WoW window", f"couldn't read the window: {e}")
        cap.close()
        return None
    if size != want:
        report("FAIL", "WoW window size", f"{size[0]}x{size[1]}, need {want[0]}x{want[1]} fullscreen")
        cap.close()
        return None
    report("OK", "WoW window", f"{size[0]}x{size[1]}")
    return cap


def check_capture(cap, titles) -> bool:
    import cv2
    from vision.classify import State, classify
    try:
        frame = cap.capture()
    except Exception as e:
        report("FAIL", "Screen capture", str(e))
        return False
    brightness = float(frame.mean())
    out = ROOT / "logs" / "preflight_frame.png"
    out.parent.mkdir(exist_ok=True)
    cv2.imwrite(str(out), frame)
    if brightness < 3:
        report("WARN", "Screen capture", f"frame is almost black (is WoW minimized?), saved {out}")
    else:
        report("OK", "Screen capture", f"saved {out.relative_to(ROOT)}: open it and check it looks right")
    # One frame may be a loading screen or a fade: keep looking for a while.
    deadline = time.monotonic() + RECOGNIZE_FOR
    while True:
        t = time.monotonic()
        obs = classify(frame, titles)
        ms = (time.monotonic() - t) * 1000
        if obs.state is not State.UNKNOWN or time.monotonic() >= deadline:
            break
        print(f"  screen not recognized yet ({obs.text[:50]!r}), looking again...")
        time.sleep(1)
        frame = cap.capture()
    if obs.state is State.UNKNOWN:
        cv2.imwrite(str(out), frame)
        report("WARN", "Screen recognition",
               f"nothing recognized for {RECOGNIZE_FOR:.0f} s (read: {obs.text[:60]!r}). Check that the "
               f"game is in English with the required settings, and open {out.relative_to(ROOT)}")
    else:
        report("OK", "Screen recognition", f"{obs.state.value.upper()} in {ms:.0f} ms")
    return True


def check_focus() -> None:
    from input import active_window_name
    print("\n  Now click into an empty spot of the WoW window (15 s)...")
    deadline = time.monotonic() + 15
    name = None
    while time.monotonic() < deadline:
        name = active_window_name()
        if name == WINDOW_NAME:
            break
        time.sleep(0.5)
    if name == WINDOW_NAME:
        report("OK", "Focus detection", "the bot can tell when WoW has focus (it pauses when it doesn't)")
    elif name is None:
        report("WARN", "Focus detection", "can't detect focus here; the bot relies on you clicking into WoW")
    else:
        report("WARN", "Focus detection", f"focused window is {name!r}, not WoW (if WoW really was "
               "focused, run the bot with --no-focus-check)")
    time.sleep(0.5)


def check_mouse(cap) -> None:
    """Move (never click) the mouse to the top edge of WoW and check where it landed."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            from vision.capture.win32 import client_rect
            left, top, _, _ = client_rect(cap.window_id)
            ctypes.windll.user32.SetCursorPos(left + 960, top + 5)
            time.sleep(0.2)
            pt = wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            dx, dy = pt.x - (left + 960), pt.y - (top + 5)
        else:
            wid = str(cap.window_id)

            def shell(command: str, *args: str) -> dict:
                # --shell must come right after the command: `xdotool getwindowgeometry --shell <wid>`
                out = subprocess.run(["xdotool", command, "--shell", *args],
                                     capture_output=True, text=True, timeout=3)
                values = dict(l.split("=", 1) for l in out.stdout.split() if "=" in l)
                if "X" not in values or "Y" not in values:
                    raise RuntimeError(f"`xdotool {command}` gave no position: "
                                       f"{(out.stderr or out.stdout).strip()!r}")
                return values

            win = shell("getwindowgeometry", wid)
            before = shell("getmouselocation")
            subprocess.run(["xdotool", "mousemove", "--window", wid, "960", "5"], check=True, timeout=3)
            time.sleep(0.2)
            mouse = shell("getmouselocation")
            if (mouse["X"], mouse["Y"]) == (before["X"], before["Y"]):
                # Wayland (XWayland): the warp reaches WoW, but the reported position is the
                # compositor's cursor, which doesn't follow it. Nothing to measure here.
                report("OK", "Mouse input", "xdotool works; the position can't be measured under "
                       "Wayland, so the practice run is the real test (watch that clicks land)")
                return
            dx = int(mouse["X"]) - (int(win["X"]) + 960)
            dy = int(mouse["Y"]) - (int(win["Y"]) + 5)
    except Exception as e:
        report("FAIL", "Mouse input", f"couldn't move the mouse over WoW: {e}")
        return
    if abs(dx) <= 2 and abs(dy) <= 2:
        report("OK", "Mouse input", "the mouse reaches the WoW window at the right spot")
    else:
        report("WARN", "Mouse input", f"mouse landed {dx:+d},{dy:+d} px off (display scaling?)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="characters.yaml")
    args = ap.parse_args()
    print("Checking your setup...\n")

    base = check_python() and check_imports()
    tools = check_tools() if base else False
    conf = check_config(ROOT / args.config) if base else None
    cap = check_window() if tools else None
    if cap is not None:
        try:
            check_focus()  # first: on Windows the capture sees whatever is in front
            check_capture(cap, conf[2] if conf else ["Normal", "PvP", "Roleplay", "Roleplay PvP"])
            check_mouse(cap)
        finally:
            cap.close()

    fails = sum(s == "FAIL" for s, _, _ in results)
    warns = sum(s == "WARN" for s, _, _ in results)
    print()
    if fails:
        print(f"Not ready: {fails} problem(s) to fix (see FAIL above).")
        return 1
    print("Ready." + (f" {warns} warning(s): read them, they may be fine." if warns else ""))
    print("Next: a practice run that stops before Finish:  python -m runner --config", args.config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
