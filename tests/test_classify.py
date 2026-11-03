"""classify() against every full-frame fixture. Expected state = longest matching filename prefix.

Only fixtures/ is scanned (not approx/ or tmp/), and only 1920x1080 frames are used.
"""
from pathlib import Path

import cv2
import numpy as np
import pytest

from vision.classify import State, char_screen, classify

FIXTURES = Path(__file__).parent / "fixtures"
# key -> UI title, as in races.yaml `rulesets`
RULESETS = {"normal": "Normal", "pvp": "PvP", "roleplay": "Roleplay", "roleplay_pvp": "Roleplay PvP"}
TITLES = list(RULESETS.values())

PREFIXES = {
    "connecting": State.LOGGING_IN,
    "logging_in": State.LOGGING_IN,       # other busy dialogs, e.g. over char select
    "reconnect": State.RECONNECT,
    "login_form": State.LOGIN_FORM,
    "ruleset_select": State.RULESET_SELECT,
    "char_select": State.CHAR_SELECT,
    "char_create": State.CHAR_CREATE,
    "char_customize": State.CHAR_CUSTOMIZE,
    "unknown": State.UNKNOWN,
    "fatal": State.FATAL,
    "error_dialog": State.ERROR,       # full frames only; crops live in approx/
    "error_dialog_no_license": State.FATAL,   # in FATAL_ERRORS: retrying can't help
    "realm_list": State.REALM_LIST,
}


def _longest(name: str, keys) -> str | None:
    hits = [k for k in keys if name.startswith(k)]
    return max(hits, key=len) if hits else None


def _full_frames(prefix: str = ""):
    out = []
    for p in sorted(FIXTURES.glob(f"{prefix}*.png")):
        if _longest(p.name, PREFIXES) is None:
            continue
        img = cv2.imread(str(p))
        if img is not None and img.shape[:2] == (1080, 1920):
            out.append(p)
    return out


@pytest.mark.parametrize("path", _full_frames(), ids=lambda p: p.name)
def test_classify_fixture(path):
    obs = classify(cv2.imread(str(path)), TITLES)
    assert obs.state is PREFIXES[_longest(path.name, PREFIXES)], obs.text


def test_small_frame_does_not_crash():
    # e.g. a crop saved under a full-frame name: no dialog box fits, nothing should raise
    small = cv2.imread(str(FIXTURES / "approx" / "fatal_max_characters_01.png"))
    if small is None:
        pytest.skip("no approx/fatal_max_characters_01.png")
    classify(small, TITLES)


def test_black_frame_is_unknown():
    assert classify(np.zeros((1080, 1920, 3), np.uint8), TITLES).state is State.UNKNOWN


def _ruleset_in_name(name: str) -> str | None:
    """char_select_[empty_]<ruleset>_NN.png -> UI title, e.g. 'Roleplay PvP'."""
    rest = name.removeprefix("char_select_").removeprefix("empty_")
    key = _longest(rest, [k + "_" for k in RULESETS])
    return RULESETS[key[:-1]] if key else None


@pytest.mark.parametrize(
    "path", [p for p in _full_frames("char_select_") if _ruleset_in_name(p.name)],
    ids=lambda p: p.name,
)
def test_char_select_reads_ruleset_title(path):
    obs = classify(cv2.imread(str(path)), TITLES)
    assert obs.ruleset is not None, obs.text
    assert obs.ruleset.title == _ruleset_in_name(path.name)


# Bottom-band text as actually read on the live fixtures.
@pytest.mark.parametrize("bottom, state", [
    ("create character back", State.CHAR_SELECT),
    ("highdefinition models 4 back", State.CHAR_CUSTOMIZE),
    ("highdefinition models back", State.CHAR_CUSTOMIZE),
    ("highdefinition models", State.CHAR_CUSTOMIZE),
    ("highdefinition models customize back", State.CHAR_CREATE),
    ("customize back", State.CHAR_CREATE),
    # live, right after selecting an empty ruleset: no "customize" read, class bar is
    ("highdefinition 4 back 2 warrior hunter mage rogue priest warlock palad", State.CHAR_CREATE),
    ("n 4 highdefinition models i 4 back 2 y customize hunter mage rogue war", State.CHAR_CREATE),
    ("highdefinition models mage back", State.CHAR_CUSTOMIZE),   # one stray word isn't enough
    ("cancel select", None),
    ("", None),
])
def test_char_screen_from_bottom_text(bottom, state):
    assert char_screen(bottom) is state
