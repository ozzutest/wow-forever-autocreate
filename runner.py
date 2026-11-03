"""Launch-night bot: from anywhere between login and char customize to created characters.

Usage (from the project root, WoW running fullscreen at 1920x1080):
    python -m runner                               # dry run: stops right before Finish
    python -m runner --create                      # the real thing
    python -m runner --config characters.test.yaml
    python -m runner --create --rehearse-error throttled   # full account: test the retry loop

You can start it right after launching WoW: it waits for the window. Then click into an
empty spot of the WoW window; the bot starts as soon as WoW has focus, and pauses whenever
WoW loses focus.
Logs: logs/run_*.log; screenshots of every error and unknown screen: logs/frames/.
Ctrl+C stops it.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

from config import load_characters, load_game
from config.models import ModelType
from input.xdotool import XdotoolInput, active_window_name
from scripts.slice_orc import Bot
from states import policy
from states.context import Context
from states.handlers import Machine, Outcome
from vision.capture import make_capture
from vision.classify import State, classify

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
log = logging.getLogger("bot")

WINDOW_NAME = "World of Warcraft"
POLL = 1.0           # s between frames (classify itself takes up to ~1 s)
ERROR_PAUSE = 5.0    # s after an unexpected exception before trying again
WINDOW_WAIT = 300.0  # s to wait for the WoW window (and fullscreen size) at startup


def wow_focused() -> bool | None:
    """True/False if we can tell whether WoW has keyboard focus, None if we can't."""
    name = active_window_name()
    return None if name is None else name == WINDOW_NAME


def wait_for_window(size: tuple[int, int], timeout: float):
    """Capture for the WoW window once it exists at the right size, or None on timeout."""
    deadline, last_log = time.monotonic() + timeout, 0.0
    while True:
        try:
            cap = make_capture(WINDOW_NAME)
            if cap.size() == size:
                return cap
            why = f"window is {cap.size()}, need {size} fullscreen"
            cap.close()
        except Exception as e:  # no window yet, DISPLAY not set, ...
            why = str(e) or type(e).__name__
        if time.monotonic() >= deadline:
            log.error("no usable WoW window: %s", why)
            return None
        if time.monotonic() - last_log >= 15:
            last_log = time.monotonic()
            log.info("waiting for the WoW window (%s)", why)
        time.sleep(2)


def wait_for_focus(seconds: float) -> None:
    """Return as soon as WoW has focus, or after `seconds` (fallback countdown)."""
    end = time.monotonic() + seconds
    focused = None
    while (left := end - time.monotonic()) > 0:
        focused = wow_focused()
        if focused:
            print()
            log.info("WoW has focus: starting")
            return
        print(f"\rclick into an empty spot of the WoW window... {left:4.0f} s ", end="", flush=True)
        time.sleep(0.5)
    print()
    if focused is None:
        log.warning("can't detect focus here: assuming WoW is focused")
    else:
        log.warning("WoW doesn't have focus; the bot pauses until you click into it")


def _value(x):
    return getattr(x, "value", x)


def _title(entry) -> str:
    """UI title of a races.yaml ruleset entry: a plain string, or a model with a title field.

    Careful: every str has a .title *method*, so getattr(entry, "title") is wrong for strings.
    """
    if isinstance(entry, str):
        return entry
    title = getattr(entry, "title", None)
    if not isinstance(title, str):
        raise TypeError(f"can't get a ruleset title from {entry!r}")
    return title


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="characters.yaml")
    ap.add_argument("--create", action="store_true", help="click Finish (default: dry run)")
    ap.add_argument("--focus-delay", type=float, default=60.0,
                    help="max seconds to wait for you to click into WoW (starts as soon as it's focused)")
    ap.add_argument("--no-focus-check", action="store_true",
                    help="don't pause when WoW seems unfocused (if focus detection misreports here)")
    ap.add_argument("--rehearse-error", choices=policy.REHEARSABLE, metavar="KEY",
                    help="testing on a full account: treat the 'maximum number of characters' "
                         "popup after Finish as this error and keep retrying "
                         f"({', '.join(policy.REHEARSABLE)}); needs --create")
    args = ap.parse_args()
    if args.rehearse_error and not args.create:
        ap.error("--rehearse-error needs --create: the popup only appears after Finish")

    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d %(levelname).1s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(LOG_DIR / f"run_{datetime.now():%Y%m%d_%H%M%S}.log")],
    )

    game = load_game(ROOT / "config" / "races.yaml")
    cfg = load_characters(ROOT / args.config, game)
    for i, ch in enumerate(cfg.characters):
        model = ModelType.HD if ch.hd_model else ModelType.SD
        if game.races[ch.race].appearance.get(model, {}).get(ch.gender) is None:
            log.error("character #%d: no %s %s %s data in races.yaml", i, model.value, _value(ch.gender), ch.race)
            return 2
    titles = [_title(t) for t in game.rulesets.values()]
    target = _title(game.rulesets[_value(cfg.ruleset)])
    layout = yaml.safe_load((ROOT / "vision" / "layout.yaml").read_text(encoding="utf-8"))

    capture = wait_for_window(tuple(layout["window_size"]), WINDOW_WAIT)
    if capture is None:
        return 2

    ctx = Context(dry_run=not args.create, rehearse_fatal_as=args.rehearse_error)
    bot = Bot(capture, XdotoolInput(capture.window_id), layout)
    machine = Machine(bot, game, cfg.characters, target, titles, layout, ctx, LOG_DIR / "frames")
    log.info("%d character(s), ruleset %r%s", len(cfg.characters), target,
             " [DRY RUN: stops before Finish]" if ctx.dry_run else " [CREATE]")
    if ctx.rehearse_fatal_as:
        log.warning("REHEARSAL: the character-limit popup counts as %r. Only run this on a FULL "
                    "account, or Finish will really create the character. Ctrl+C to stop.",
                    ctx.rehearse_fatal_as)
    wait_for_focus(args.focus_delay)

    last: State | None = None
    paused = False
    try:
        while True:
            # Never send input to another window (notification, screen lock, terminal).
            if not args.no_focus_check and wow_focused() is False:
                if not paused:
                    log.warning("WoW lost focus: paused (click into WoW to continue)")
                    paused = True
                time.sleep(POLL)
                continue
            if paused:
                log.info("WoW focused again: resuming")
                paused = False
            try:
                frame = capture.capture()
                obs = classify(frame, titles)
                if obs.state is not last:
                    log.info("== %s%s", obs.state.value.upper(), f"  ({obs.text[:70]})" if obs.text else "")
                    last = obs.state
                outcome = machine.handle(obs, frame)
            except KeyboardInterrupt:
                raise
            except Exception:
                # Never die overnight: log it, wait, look at the screen again.
                log.exception("unexpected error, retrying in %.0f s", ERROR_PAUSE)
                time.sleep(ERROR_PAUSE)
                continue
            if outcome is Outcome.DONE:
                log.info("done")
                return 0
            if outcome is Outcome.STOP:
                log.error("stopped: needs a human")
                return 1
            time.sleep(POLL)
    except KeyboardInterrupt:
        log.info("interrupted")
        return 130
    finally:
        capture.close()


if __name__ == "__main__":
    sys.exit(main())
