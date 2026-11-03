"""Which screen is the client on? One call per frame, cheapest decisive check first.

Order matters:
1. Dialogs (they overlay every screen): read_dialog on the center box.
2. Center screens without a dialog: Reconnect, login form (same OCR pass, reused).
3. Ruleset select: the gold subtitle under "Choose Your Gameplay Ruleset".
4. Char screens: labels in the bottom band (see char_screen()).
5. Anything else is UNKNOWN (loading screens, fades): the runner waits.

Calibrated on 1920x1080 fixtures. Worst case is three OCR passes, about 1 s per frame,
which is plenty for a bot that mostly waits.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

from vision.ocr import (
    DIALOG_BOX, Dialog, DialogKind, RulesetCard, fuzzy_contains, normalize,
    read_dialog, read_lines, read_rulesets,
)

SUBTITLE_BOX = (560, 180, 1360, 260)          # "You will only be able to interact ... same ruleset."
BOTTOM_BAND = (0, 940, 1920, 1080)            # Back, Create Character, Customize, Finish, Select...
CHAR_SELECT_TITLE_BAND = (1530, 115, 1910, 145)  # ruleset title above the character list

# Bottom-band labels, normalized ("High-Definition" -> "highdefinition")
CREATE_CHARACTER_LABEL = "create character"
CUSTOMIZE_LABEL = "customize"
FINISH_LABEL = "finish"
HD_LABEL = "highdefinition models"
# The class bar is in the bottom band on char create only. Two names must match, so one
# stray OCR word can't turn customize into char create.
CLASS_LABELS = ("warrior", "hunter", "mage", "rogue", "priest", "warlock", "paladin", "druid", "shaman")
CLASS_LABELS_MIN = 2


class State(Enum):
    LOGGING_IN = "logging_in"
    QUEUE = "queue"
    ERROR = "error"
    FATAL = "fatal"
    RECONNECT = "reconnect"
    LOGIN_FORM = "login_form"
    RULESET_SELECT = "ruleset_select"
    CHAR_SELECT = "char_select"
    CHAR_CREATE = "char_create"
    CHAR_CUSTOMIZE = "char_customize"
    UNKNOWN = "unknown"


_DIALOG_STATES = {
    DialogKind.BUSY: State.LOGGING_IN,
    DialogKind.QUEUE: State.QUEUE,
    DialogKind.ERROR: State.ERROR,
    DialogKind.FATAL: State.FATAL,
}


@dataclass
class Observation:
    state: State
    dialog: Dialog | None = None          # set for dialog states
    ruleset: RulesetCard | None = None    # CHAR_SELECT: ruleset shown above the list
    text: str = ""                        # what was read, for the log


def _joined(frame: np.ndarray, box: tuple[int, int, int, int]) -> str:
    return " ".join(normalize(l.text) for l in read_lines(frame, box))


def classify(frame: np.ndarray, ruleset_titles: list[str]) -> Observation:
    dialog = read_dialog(frame, DIALOG_BOX)
    if dialog.kind in _DIALOG_STATES:
        return Observation(_DIALOG_STATES[dialog.kind], dialog=dialog, text=dialog.message)

    center = normalize(dialog.message)
    if fuzzy_contains(center, "email or phone") or fuzzy_contains(center, "password"):
        return Observation(State.LOGIN_FORM, text=center)
    # Right after a disconnect the Reconnect button is highlighted (white label, invisible
    # to the gold mask); the Log Out label below it stays gold.
    if fuzzy_contains(center, "reconnect") or fuzzy_contains(center, "log out"):
        return Observation(State.RECONNECT, text=center)

    subtitle = _joined(frame, SUBTITLE_BOX)
    if fuzzy_contains(subtitle, "same ruleset"):
        return Observation(State.RULESET_SELECT, text=subtitle)

    bottom = _joined(frame, BOTTOM_BAND)
    state = char_screen(bottom)
    if state is State.CHAR_SELECT:
        cards = read_rulesets(frame, ruleset_titles, band=CHAR_SELECT_TITLE_BAND)
        return Observation(state, ruleset=cards[0] if cards else None, text=bottom)
    if state is not None:
        return Observation(state, text=bottom)

    return Observation(State.UNKNOWN, text=" | ".join(t for t in (center, subtitle, bottom) if t))


def char_screen(bottom: str) -> State | None:
    """Which char screen, from the normalized bottom-band text.

    Char create and customize both show the High-Definition Models checkbox; only
    char create has the class bar and the "Customize" button, so those are checked
    first (the Customize label isn't always read, seen live right after picking an
    empty ruleset). Customize is then recognized by the HD label, not by Finish:
    Finish isn't read in the bottom band, and a disabled (grey) Finish would be
    invisible to the gold mask anyway.
    """
    if fuzzy_contains(bottom, CREATE_CHARACTER_LABEL):
        return State.CHAR_SELECT
    words = set(bottom.split())
    if sum(c in words for c in CLASS_LABELS) >= CLASS_LABELS_MIN or fuzzy_contains(bottom, CUSTOMIZE_LABEL):
        return State.CHAR_CREATE
    if fuzzy_contains(bottom, FINISH_LABEL) or fuzzy_contains(bottom, HD_LABEL):
        return State.CHAR_CUSTOMIZE
    return None
