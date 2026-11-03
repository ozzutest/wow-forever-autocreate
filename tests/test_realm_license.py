"""No-game-time account: the license error must stop the bot, the realm list must be closed."""
from pathlib import Path

import cv2

from vision.classify import State, classify
from vision.ocr import DialogKind, read_dialog

FIXTURES = Path(__file__).parent / "fixtures"
TITLES = ["Normal", "PvP", "Roleplay", "Roleplay PvP"]


def frame(name: str):
    img = cv2.imread(str(FIXTURES / name))
    assert img is not None, name
    return img


def test_no_license_is_fatal():
    d = read_dialog(frame("error_dialog_no_license_01.png"))
    assert d.kind is DialogKind.FATAL
    assert d.known == "no_license"
    assert d.error_code == "WOW51900337"   # split by OCR into "WOW519003 37", joined again
    assert d.okay is not None and abs(d.okay[0] - 959) < 15 and abs(d.okay[1] - 575) < 15


def test_no_license_classifies_as_fatal():
    assert classify(frame("error_dialog_no_license_01.png"), TITLES).state is State.FATAL


def test_realm_list():
    assert classify(frame("realm_list_01.png"), TITLES).state is State.REALM_LIST
