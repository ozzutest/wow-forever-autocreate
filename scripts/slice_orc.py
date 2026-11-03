"""Vertical slice: create one character starting from the char create screen.

Usage (from the project root, WoW on the char create screen):
    python -m scripts.slice_orc                       # dry run: stops before Finish
    python -m scripts.slice_orc --create              # actually clicks Finish
    python -m scripts.slice_orc --config characters.yaml --index 1

After starting, click into the WoW window within the countdown and don't touch anything.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import yaml

from config import load_characters, load_game
from config.models import Character, GameData, GenderAppearance, ModelType
from input import Input
from vision.capture import make_capture
from vision.detect import (
    ArrowState, NameStatus, arrow_state, button_active, checkbox_checked, class_available,
    class_selected, gender_selected, name_status, race_selected, wait_for,
)

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
log = logging.getLogger("slice")


class SliceError(RuntimeError):
    pass


class Bot:
    def __init__(self, capture, inp: Input, layout: dict) -> None:
        self.cap = capture
        self.inp = inp
        self.cc = layout["char_create"]
        self.cu = layout["char_customize"]

    # ------------------------------------------------------------ helpers

    def frame(self):
        return self.cap.capture()

    def wait(self, what: str, predicate, timeout: float):
        start = time.monotonic()
        frame = wait_for(self.cap, predicate, timeout)
        took = time.monotonic() - start
        if frame is None:
            raise SliceError(f"timeout after {timeout:.0f}s waiting for: {what}")
        log.info("  ok: %s (%.2fs)", what, took)
        return frame

    def fail_shot(self) -> Path:
        LOG_DIR.mkdir(exist_ok=True)
        path = LOG_DIR / f"fail_{datetime.now():%Y%m%d_%H%M%S}.png"
        cv2.imwrite(str(path), self.frame())
        return path

    # ----------------------------------------------------------- char create

    def check_char_create(self) -> None:
        frame = self.frame()
        selected = [r for r, c in self.cc["race"].items() if race_selected(frame, tuple(c))]
        if len(selected) != 1:
            raise SliceError(f"not on the char create screen (selected races: {selected})")
        log.info("char create screen detected (preselected: %s)", selected[0])

    def select_race(self, race: str) -> None:
        center = tuple(self.cc["race"][race])
        log.info("race -> %s", race)
        if not race_selected(self.frame(), center):
            self.inp.click(*center)
        self.wait(f"race {race} selected", lambda f: race_selected(f, center), 5)

    def select_gender(self, gender: str) -> None:
        center = tuple(self.cc["gender"][gender])
        log.info("gender -> %s", gender)
        if not gender_selected(self.frame(), center):
            self.inp.click(*center)
        self.wait(f"gender {gender} selected", lambda f: gender_selected(f, center), 5)

    def check_classes(self, game: GameData, race: str) -> None:
        frame = self.frame()
        y = self.cc["class_y"]
        on_screen = {c for c, x in self.cc["class_x"].items() if class_available(frame, (x, y))}
        expected = {c.value for c in game.races[race].classes}
        if on_screen != expected:
            raise SliceError(
                f"class bar doesn't match races.yaml for {race}: "
                f"screen={sorted(on_screen)} yaml={sorted(expected)}"
            )
        log.info("class bar matches races.yaml")

    def select_class(self, cls: str) -> None:
        center = (self.cc["class_x"][cls], self.cc["class_y"])
        log.info("class -> %s", cls)
        if not class_selected(self.frame(), center):
            self.inp.click(*center)
        self.wait(f"class {cls} selected", lambda f: class_selected(f, center), 5)

    def set_hd(self, hd: bool) -> None:
        center = tuple(self.cc["hd_checkbox"])
        log.info("HD models -> %s", hd)
        if checkbox_checked(self.frame(), center) != hd:
            self.inp.click(*center)
        self.wait(f"HD checkbox = {hd}", lambda f: checkbox_checked(f, center) == hd, 5)

    def open_customize(self, looks: GenderAppearance) -> None:
        log.info("customize")
        self.inp.click(*self.cc["customize"])
        first = self.row_point("left", 0, looks)
        self.wait("customize panel", lambda f: arrow_state(f, first) != ArrowState.ABSENT, 15)

    # ------------------------------------------------------------ customize

    def first_row_y(self, looks: GenderAppearance) -> int:
        key = "multi_tab" if len(looks.tabs) > 1 else "single_tab"
        return self.cu["first_row_y"][key]

    def row_point(self, side: str, row: int, looks: GenderAppearance) -> tuple[int, int]:
        return self.cu["arrow_x"][side], self.first_row_y(looks) + self.cu["row_step"] * row

    def count_rows(self, frame, looks: GenderAppearance) -> int:
        n = 0
        while n < self.cu["max_rows"] and arrow_state(frame, self.row_point("left", n, looks)) != ArrowState.ABSENT:
            n += 1
        return n

    def value_boxes(self, frame, looks: GenderAppearance) -> list[np.ndarray]:
        """The middle field of each row on the open tab (swatch or option name)."""
        x0, x1 = self.cu["value_x"]
        ys = [self.first_row_y(looks) + self.cu["row_step"] * r for r in range(self.count_rows(frame, looks))]
        return [frame[y - 14 : y + 14, x0:x1].astype(np.int16) for y in ys]

    @staticmethod
    def values_changed(before: list[np.ndarray], after: list[np.ndarray]) -> bool:
        # Calibrated: an unchanged value box differs by exactly 0.0 between frames
        # (the panel is opaque there); a changed one by 1.5+.
        if len(before) != len(after):
            return True
        return any(np.abs(a - b).mean() > 0.5 for a, b in zip(before, after))

    def open_tab(self, tab: int, looks: GenderAppearance) -> None:
        expected = len(looks.tabs[tab])
        if len(looks.tabs) > 1:
            self.inp.click(*self.cu["tabs"][tab])
        self.wait(f"tab {tab + 1} with {expected} rows",
                  lambda f: self.count_rows(f, looks) == expected, 5)

    def set_field(self, field: str, target: int, looks: GenderAppearance) -> None:
        tab, row, count = looks.locate(field)
        log.info("%s -> %d of %d (tab %d, row %d)", field, target, count, tab + 1, row)
        left, right = self.row_point("left", row, looks), self.row_point("right", row, looks)

        for _ in range(count):  # reset to option 1
            if arrow_state(self.frame(), left) == ArrowState.INACTIVE:
                break
            self.inp.click(*left)
        if arrow_state(self.frame(), left) != ArrowState.INACTIVE:
            raise SliceError(f"{field}: never reached option 1")

        for _ in range(target - 1):
            self.inp.click(*right)

        right_grey = arrow_state(self.frame(), right) == ArrowState.INACTIVE
        if right_grey != (target == count):
            raise SliceError(f"{field}: right arrow state doesn't fit count {count} in races.yaml")

    def apply_appearance(self, char: Character, looks: GenderAppearance) -> None:
        self.open_tab(0, looks)  # also checks tab 1's row count against races.yaml
        if char.appearance == "random":
            log.info("appearance -> random (dice)")
            before = self.value_boxes(self.frame(), looks)
            self.inp.click(*self.cu["dice"])
            self.wait("appearance changed after dice",
                      lambda f: self.values_changed(before, self.value_boxes(f, looks)), 5)
            return
        for tab_index in range(len(looks.tabs)):
            fields = [f for f in looks.tabs[tab_index] if f in char.appearance]
            if not fields:
                continue
            self.open_tab(tab_index, looks)
            for field in fields:
                self.set_field(field, char.appearance[field], looks)

    # ----------------------------------------------------------------- names

    def try_names(self, char: Character) -> bool:
        indicator = tuple(self.cu["name_indicator"])
        for i, pair in enumerate(char.names, 1):
            log.info("name %d/%d: %s %s", i, len(char.names), pair.main, pair.secondary)
            for point in (self.cu["name_main"], self.cu["name_secondary"]):
                self.inp.click(*point)
                self.inp.clear_field()
            # an old red X must disappear first, or we'd read a stale result
            self.wait("name fields cleared",
                      lambda f: name_status(f, indicator) == NameStatus.PENDING, 5)

            self.inp.click(*self.cu["name_main"])
            self.inp.type_text(pair.main)
            self.inp.click(*self.cu["name_secondary"])
            self.inp.type_text(pair.secondary)

            try:
                frame = self.wait("name check result",
                                  lambda f: name_status(f, indicator) != NameStatus.PENDING, 15)
            except SliceError as e:
                raise SliceError(f"{e}: no indicator after typing; is WoW focused?") from e
            if name_status(frame, indicator) == NameStatus.OK:
                log.info("  name accepted")
                return True
            log.info("  name rejected")
        return False

    def finish(self) -> None:
        center = tuple(self.cu["finish"])
        self.wait("Finish active", lambda f: button_active(f, center), 5)
        log.info("clicking Finish")
        self.inp.click(*center)
        # Back on char select the customize rows are gone. Until the OCR success
        # check exists, confirm the character in the list by eye.
        left_x = self.cu["arrow_x"]["left"]
        rows = [(left_x, y) for y in self.cu["first_row_y"].values()]
        self.wait("customize screen gone",
                  lambda f: all(arrow_state(f, p) == ArrowState.ABSENT for p in rows), 30)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="characters.example.yaml")
    ap.add_argument("--index", type=int, default=0, help="which character in the config")
    ap.add_argument("--create", action="store_true", help="click Finish (default: dry run)")
    ap.add_argument("--focus-delay", type=float, default=10.0)
    args = ap.parse_args()

    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(LOG_DIR / f"slice_{datetime.now():%Y%m%d_%H%M%S}.log")],
    )

    game = load_game(ROOT / "config" / "races.yaml")
    char = load_characters(ROOT / args.config, game).characters[args.index]
    model = ModelType.HD if char.hd_model else ModelType.SD
    looks = game.races[char.race].appearance.get(model, {}).get(char.gender)
    if looks is None:
        log.error("no %s %s %s data in races.yaml", model.value, char.gender.value, char.race)
        return 2
    layout = yaml.safe_load((ROOT / "vision" / "layout.yaml").read_text(encoding="utf-8"))

    capture = make_capture("World of Warcraft")
    if capture.size() != tuple(layout["window_size"]):
        log.error("window is %s, layout expects %s", capture.size(), layout["window_size"])
        return 2
    try:
        window_id = capture.window_id
    except AttributeError:
        log.error("capture backend has no window_id property (add it to X11Capture)")
        return 2

    bot = Bot(capture, Input(window_id), layout)
    log.info("character: %s %s %s (%s)%s", char.gender.value, char.race, char.char_class.value,
             model.value, "" if args.create else " [dry run]")
    for s in range(int(args.focus_delay), 0, -1):
        print(f"\rclick into WoW now... {s} ", end="", flush=True)
        time.sleep(1)
    print()

    t0 = time.monotonic()
    try:
        bot.check_char_create()
        bot.select_race(char.race)
        bot.select_gender(char.gender.value)
        bot.check_classes(game, char.race)
        bot.select_class(char.char_class.value)
        bot.set_hd(char.hd_model)
        bot.open_customize(looks)
        bot.apply_appearance(char, looks)
        if not bot.try_names(char):
            log.error("all names rejected")
            return 1
        if not args.create:
            log.info("dry run: stopping before Finish (%.1fs total)", time.monotonic() - t0)
            return 0
        bot.finish()
        log.info("done in %.1fs; check char select by eye", time.monotonic() - t0)
        return 0
    except SliceError as e:
        log.error("FAILED: %s (screenshot: %s)", e, bot.fail_shot())
        return 1
    finally:
        capture.close()


if __name__ == "__main__":
    sys.exit(main())