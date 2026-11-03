"""Dialog OCR against the screenshot fixtures.

Layout:
  fixtures/         full 1920x1080 window frames from scripts.grab_fixture: the
                    exact input the bot sees at runtime.
  fixtures/approx/  screenshots from the desktop screenshot tool. The error dialogs
                    and queue dialogs here are hand-cut crops: not in window
                    coordinates, possibly another scale. They're read with box=None
                    and only their text is asserted, never positions.

Naming: error_dialog_<key>_NN.png, name_error_<anything>_NN.png, queue_NN.png.
Every new screenshot named this way becomes a test case automatically.
"""
import re
from pathlib import Path

import cv2
import pytest

from vision.ocr import KNOWN_ERRORS, DialogKind, match_known, read_dialog

FIXTURES = Path(__file__).parent / "fixtures"
APPROX = FIXTURES / "approx"

ERROR_RE = re.compile(r"error_dialog_(.+?)_\d+\.png$")
# Filename key -> KNOWN_ERRORS key, where the screenshot was named before the key existed.
KEY_ALIASES = {"disconnect": "disconnected"}

# Positions read off the screenshots; a fixture not listed only needs *a* position.
QUEUE_POSITIONS = {"queue_01.png": 260, "queue_02.png": 16862}


def _files(folder: Path, pattern: str) -> list[Path]:
    return sorted(folder.glob(pattern))


def _load(path: Path):
    img = cv2.imread(str(path))
    assert img is not None, f"can't read {path}"
    return img


@pytest.mark.parametrize("path", _files(APPROX, "error_dialog_*.png"), ids=lambda p: p.name)
def test_error_dialog(path):
    key = ERROR_RE.search(path.name).group(1)
    expected = KEY_ALIASES.get(key, key)
    assert expected in KNOWN_ERRORS, f"rename {path.name} or add an alias: no error key {key!r}"
    d = read_dialog(_load(path), box=None)
    assert d.kind is DialogKind.ERROR, d.message
    assert d.known == expected, d.message
    assert d.okay is not None, "no Okay found (neither OCR nor red-button fallback)"


@pytest.mark.parametrize("path", _files(APPROX, "queue_*.png"), ids=lambda p: p.name)
def test_queue_dialog(path):
    d = read_dialog(_load(path), box=None)
    assert d.kind is DialogKind.QUEUE, d.message
    assert d.queue_position is not None
    assert d.queue_position == QUEUE_POSITIONS.get(path.name, d.queue_position)


@pytest.mark.parametrize("path", _files(FIXTURES, "connecting_*.png"), ids=lambda p: p.name)
def test_logging_in_dialog(path):
    d = read_dialog(_load(path))  # full frames: default DIALOG_BOX
    assert d.kind is DialogKind.BUSY, d.message


@pytest.mark.parametrize("text, key", [
    ("this faction is currently unavailable on this ruleset due to population imbalanee", "faction_imbalance"),
    ("creation of that type of character is currently throttied", "throttled"),
    ("character creation timed out", "timed_out"),
    ("you have been disconnected from the server", "disconnected"),
    ("no realms are currently available", "no_realms"),
    ("you cant use the same letter three times", "name_rule"),
    ("some message we have never seen", None),
])
def test_match_known_tolerates_ocr_typos(text, key):
    assert match_known(text) == key