"""Before-run check: is everything the bot needs working on this machine, right now?

Usage (from the project root, with WoW running fullscreen):
    python -m preflight
    python -m preflight --config characters.yaml

It never clicks or types. The only input it sends is a mouse *move* over the top edge
of the WoW window, to prove the input tool can reach the window.
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
        report("OK", "Tesseract OCR", str(pytesseract.get_tesseract_version()))
    except Exception as e:
        report("FAIL", "Tesseract OCR", f"{e} (install: sudo apt install tesseract-ocr)")
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
        from vision.capture import make_capture
        cap = make_capture(WINDOW_NAME)
    except Exception as e:
        report("FAIL", "WoW window", f"not found ({e}). Is WoW running?")
        return None
    size = cap.size()
    if size != want:
        report("FAIL", "WoW window size", f"{size[0]}x{size[1]}, need {want[0]}x{want[1]} fullscreen")
        cap.close()
        return None
    report("OK", "WoW window", f"{size[0]}x{size[1]}")
    return cap


def check_capture(cap, titles) -> bool:
    import cv2
    from vision.classify import classify
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
    t = time.monotonic()
    obs = classify(frame, titles)
    ms = (time.monotonic() - t) * 1000
    status = "WARN" if obs.state.value == "unknown" else "OK"
    report(status, "Screen recognition", f"{obs.state.value.upper()} in {ms:.0f} ms"
           + (" (fine on loading screens; go to Reconnect or char select and run again)" if status == "WARN" else ""))
    return True


def check_input(cap) -> None:
    if not sys.platform.startswith("linux"):
        report("WARN", "Mouse input", "only checked on Linux so far")
        return
    wid = str(cap.window_id)

    def shell(*args) -> dict:
        out = subprocess.run(["xdotool", *args, "--shell"], capture_output=True, text=True, timeout=3)
        return dict(l.split("=", 1) for l in out.stdout.split() if "=" in l)

    try:
        win = shell("getwindowgeometry", wid)
        subprocess.run(["xdotool", "mousemove", "--window", wid, "960", "5"], check=True, timeout=3)
        time.sleep(0.2)
        mouse = shell("getmouselocation")
        dx = int(mouse["X"]) - (int(win["X"]) + 960)
        dy = int(mouse["Y"]) - (int(win["Y"]) + 5)
    except Exception as e:
        report("FAIL", "Mouse input", f"xdotool couldn't move the mouse over WoW: {e}")
        return
    if abs(dx) <= 2 and abs(dy) <= 2:
        report("OK", "Mouse input", "the mouse reaches the WoW window at the right spot")
    else:
        report("WARN", "Mouse input", f"mouse landed {dx:+d},{dy:+d} px off (display scaling on?)")

    from input.xdotool import active_window_name
    print("\n  Now click into an empty spot of the WoW window (10 s)...")
    deadline = time.monotonic() + 10
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
        report("WARN", "Focus detection", f"focused window is {name!r}, not WoW")


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
            check_capture(cap, conf[2] if conf else ["Normal", "PvP", "Roleplay", "Roleplay PvP"])
            check_input(cap)
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
