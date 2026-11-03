from pathlib import Path

import cv2
import pytest

from vision.ocr import DialogKind, read_dialog, read_rulesets

FIX = Path(__file__).resolve().parent / "fixtures"
APPROX = FIX / "approx"  # crops/screenshots at other sizes: OCR the whole image


def load(path):
    img = cv2.imread(str(path))
    assert img is not None, f"missing fixture {path}"
    return img


@pytest.mark.parametrize("name, message", [
    ("connecting_01.png", "Connecting"),
    ("connecting_02.png", "Retrieving server information"),
    ("connecting_03.png", "Logging in to game server"),
])
def test_busy_dialogs(name, message):
    d = read_dialog(load(FIX / name))
    assert d.kind is DialogKind.BUSY
    assert message in d.message


@pytest.mark.parametrize("name", ["reconnect_01.png", "ruleset_select_01.png", "char_create_orc_hd_01.png"])
def test_no_dialog(name):
    assert read_dialog(load(FIX / name)).kind is DialogKind.NONE


def test_queue():
    d = read_dialog(load(APPROX / "queue_01.png"), box=None)
    assert d.kind is DialogKind.QUEUE
    assert d.queue_position == 260

def test_queue():
    d = read_dialog(load(APPROX / "queue_02.png"), box=None)
    assert d.kind is DialogKind.QUEUE
    assert d.queue_position == 16862

@pytest.mark.parametrize("name, known", [
    ("error_dialog_faction_imbalance_01.png", "faction_imbalance"),
    ("error_dialog_timed_out_01.png", "timed_out"),
    ("error_dialog_throttled_01.png", "throttled"),
    ("error_dialog_no_realms_01.png", "no_realms"),
])
def test_known_errors(name, known):
    d = read_dialog(load(APPROX / name), box=None)
    assert d.kind is DialogKind.ERROR
    assert d.known == known
    assert d.okay is not None


@pytest.mark.parametrize("name", ["ruleset_select_unavailable_01.png", "ruleset_select_unavailable_02.png"])
def test_ruleset_screens_are_not_dialogs(name):
    assert read_dialog(load(APPROX / name), box=None).kind is DialogKind.NONE


def test_error_with_code():
    d = read_dialog(load(APPROX / "error_dialog_no_realms_01.png"), box=None)
    assert d.kind is DialogKind.ERROR
    assert d.error_code == "WOW51900309"
    assert d.okay is not None




def test_no_license_is_fatal_with_split_code():
    # Account without game time. OCR reads the code as "WOW519003 37": the digits are joined.
    d = read_dialog(load(FIX / "error_dialog_no_license_01.png"))
    assert d.kind is DialogKind.FATAL
    assert d.known == "no_license"
    assert d.error_code == "WOW51900337"
    assert d.okay is not None


TITLES = ["Normal", "PvP", "Roleplay", "Roleplay PvP"]  # values of rulesets in races.yaml


@pytest.mark.parametrize("name", [
    "ruleset_select_01.png", "ruleset_select_pvp_01.png",
    "ruleset_select_roleplay_pvp_01.png",
])
def test_rulesets_all_available(name):
    cards = read_rulesets(load(FIX / name), TITLES)
    assert [c.title for c in cards] == TITLES
    assert all(c.available for c in cards)
    assert [c.center[0] for c in cards] == pytest.approx([534, 816, 1097, 1380], abs=10)


@pytest.mark.parametrize("name, band, expected", [
    # reservation night: three cards, other resolution, some greyed out
    ("ruleset_select_unavailable_01.png", (0, 240, 1194, 275),
     {"Normal": False, "PvP": False, "Roleplay": True}),
    ("ruleset_select_unavailable_02.png", (0, 272, 1228, 305),
     {"Normal": False, "PvP": False, "Roleplay": False}),
])
def test_rulesets_unavailable(name, band, expected):
    cards = read_rulesets(load(APPROX / name), TITLES, band=band)
    assert {c.title: c.available for c in cards} == expected