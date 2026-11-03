from pathlib import Path

import cv2
import pytest

from vision.detect import (
    ArrowState, NameStatus, arrow_state, button_active, checkbox_checked, name_status,
)

FIX = Path(__file__).resolve().parent / "fixtures"
LEFT, RIGHT = 1615, 1865
MULTI, SINGLE, STEP = 335, 260, 86

A, I, X = ArrowState.ACTIVE, ArrowState.INACTIVE, ArrowState.ABSENT


def load(name):
    img = cv2.imread(str(FIX / name))
    assert img is not None, f"missing fixture {name}"
    return img


@pytest.mark.parametrize("name, first_y, expected", [
    # (left, right) per row; expectations read from the screenshots
    ("char_customize_orc_male_hd_01.png", MULTI,
     [(A, A), (A, I), (A, A), (I, A), (A, A), (X, X)]),
    ("char_customize_orc_female_hd_03.png", MULTI,
     [(A, A), (A, A), (A, I), (A, A), (A, A), (X, X)]),
    ("char_customize_human_male_hd_01.png", MULTI,
     [(A, A), (A, A), (A, A), (A, I), (A, A), (A, I), (A, A)]),
    ("char_customize_orc_male_sd_01.png", SINGLE,
     [(A, I), (A, A), (A, A), (I, A), (A, A), (X, X)]),
])
def test_arrow_states(name, first_y, expected):
    frame = load(name)
    for row, (left, right) in enumerate(expected):
        y = first_y + STEP * row
        assert arrow_state(frame, (LEFT, y)) == left, f"row {row} left"
        assert arrow_state(frame, (RIGHT, y)) == right, f"row {row} right"


@pytest.mark.parametrize("name, active", [
    ("ruleset_select_01.png", False),
    ("ruleset_select_pvp_01.png", True),
    ("ruleset_select_normal_01.png", True),
])
def test_select_button(name, active):
    assert button_active(load(name), (1787, 1037)) is active


def test_customize_active_finish_disabled():
    assert button_active(load("char_create_orc_hd_01.png"), (1748, 1018))
    assert not button_active(load("char_customize_orc_male_hd_01.png"), (1748, 1018))


@pytest.mark.parametrize("name, checked", [
    ("char_create_orc_hd_01.png", True),
    ("char_create_orc_sd_01.png", False),
    ("char_create_human_sd_01.png", False),
])
def test_hd_checkbox(name, checked):
    assert checkbox_checked(load(name), (1658, 960)) is checked


@pytest.mark.parametrize("name, status", [
    ("char_customize_orc_hd_name_bad_01.png", NameStatus.BAD),
    ("char_customize_orc_hd_name_ok_01.png", NameStatus.OK),
])
def test_name_indicator(name, status):
    assert name_status(load(name), (1324, 54)) == status


RACES = {"orc": (275, 157), "undead": (275, 254), "human": (87, 157), "dwarf": (87, 254)}
CLASS_X = {"warrior": 577, "priest": 961, "paladin": 1153, "druid": 1249, "shaman": 1345}


@pytest.mark.parametrize("name, race", [
    ("char_create_orc_hd_01.png", "orc"),
    ("char_create_human_hd_01.png", "human"),
])
def test_race_selected(name, race):
    from vision.detect import race_selected
    frame = load(name)
    for key, center in RACES.items():
        assert race_selected(frame, center) is (key == race), key


@pytest.mark.parametrize("name, unavailable", [
    ("char_create_orc_hd_01.png", {"priest", "paladin", "druid"}),
    ("char_create_human_hd_01.png", {"druid", "shaman"}),
])
def test_class_availability_and_selection(name, unavailable):
    from vision.detect import class_available, class_selected
    frame = load(name)
    for cls, x in CLASS_X.items():
        assert class_available(frame, (x, 998)) is (cls not in unavailable), cls
    assert class_selected(frame, (577, 998))          # warrior preselected
    assert not class_selected(frame, (1345, 998))

