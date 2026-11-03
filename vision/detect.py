"""Color-based detectors for fixed UI elements (1920x1080 window coordinates).

Every function takes a BGR frame from WindowCapture.capture() and a point/box
from layout.yaml. Thresholds were calibrated on the fixtures in tests/fixtures.
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Callable

import cv2
import numpy as np

Point = tuple[int, int]


def _hsv(frame: np.ndarray, center: Point, half_w: int, half_h: int) -> np.ndarray:
    x, y = center
    return cv2.cvtColor(frame[y - half_h : y + half_h, x - half_w : x + half_w], cv2.COLOR_BGR2HSV)


def gold_fraction(frame: np.ndarray, center: Point, half_w: int = 14, half_h: int = 14) -> float:
    """Share of saturated gold/yellow pixels: arrows, selected frames, checkmark, button text."""
    h, s, v = cv2.split(_hsv(frame, center, half_w, half_h))
    return float(((h >= 15) & (h <= 35) & (s > 120) & (v > 150)).mean())


def red_fraction(frame: np.ndarray, center: Point, half_w: int = 14, half_h: int = 14) -> float:
    """Share of saturated red pixels: active red buttons, the name-rejected X."""
    h, s, v = cv2.split(_hsv(frame, center, half_w, half_h))
    return float((((h <= 8) | (h >= 170)) & (s > 120) & (v > 80)).mean())


def green_fraction(frame: np.ndarray, center: Point, half_w: int = 14, half_h: int = 14) -> float:
    """Share of saturated green pixels: the name-accepted check mark."""
    h, s, v = cv2.split(_hsv(frame, center, half_w, half_h))
    return float(((h >= 40) & (h <= 85) & (s > 120) & (v > 120)).mean())


def mean_saturation(frame: np.ndarray, center: Point, half_w: int = 14, half_h: int = 14) -> float:
    return float(_hsv(frame, center, half_w, half_h)[..., 1].mean())


# --------------------------------------------------------------------- arrows


class ArrowState(Enum):
    ACTIVE = "active"      # gold: can move in this direction
    INACTIVE = "inactive"  # grey: end of the list
    ABSENT = "absent"      # no arrow here (row doesn't exist, or something covers it)


def arrow_state(frame: np.ndarray, center: Point) -> ArrowState:
    # Calibrated: active ~0.19 gold; grey ~0.00 gold with saturation ~2;
    # absent rows show background (saturation 100+).
    if gold_fraction(frame, center) > 0.08:
        return ArrowState.ACTIVE
    if mean_saturation(frame, center) < 20:
        return ArrowState.INACTIVE
    return ArrowState.ABSENT


# -------------------------------------------------------------------- buttons


def button_active(frame: np.ndarray, center: Point, half_w: int = 90, half_h: int = 16) -> bool:
    """Red buttons (Select, Customize, Finish): red when active, grey when disabled.

    Calibrated: active ~0.72 red, disabled 0.00.
    """
    return red_fraction(frame, center, half_w, half_h) > 0.3


def checkbox_checked(frame: np.ndarray, center: Point) -> bool:
    """HD checkbox. Calibrated: checked ~0.145 gold, unchecked 0.00."""
    return gold_fraction(frame, center, 9, 9) > 0.05


class NameStatus(Enum):
    OK = "ok"
    BAD = "bad"
    PENDING = "pending"  # neither icon visible (yet)


def name_status(frame: np.ndarray, center: Point) -> NameStatus:
    """Indicator right of the secondary name field. Calibrated: red X ~0.31, green check ~0.17."""
    if green_fraction(frame, center, 10, 10) > 0.05:
        return NameStatus.OK
    if red_fraction(frame, center, 10, 10) > 0.05:
        return NameStatus.BAD
    return NameStatus.PENDING


# -------------------------------------------------------------------- waiting


def wait_for(
    capture,
    predicate: Callable[[np.ndarray], bool],
    timeout: float,
    interval: float = 0.25,
) -> np.ndarray | None:
    """Poll frames until predicate(frame) is true. Returns that frame, or None on timeout."""
    deadline = time.monotonic() + timeout
    while True:
        frame = capture.capture()
        if predicate(frame):
            return frame
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


# ----------------------------------------------------------- char create screen
# Calibrated on char_create fixtures: selected race/class frame ~0.55-0.61 gold
# in the ring around the icon, unselected ~0.01; selected gender ~0.26-0.37, other 0.00;
# unavailable class icons have saturation 0, available ones 90+.


def _ring_gold(frame: np.ndarray, center: Point, outer: int, inner: int) -> float:
    x, y = center
    hsv = cv2.cvtColor(frame[y - outer : y + outer, x - outer : x + outer], cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    gold = (h >= 15) & (h <= 35) & (s > 80) & (v > 170)
    ring = np.ones(gold.shape, bool)
    if inner:
        ring[outer - inner : outer + inner, outer - inner : outer + inner] = False
    return float(gold[ring].mean())


def race_selected(frame: np.ndarray, center: Point) -> bool:
    return _ring_gold(frame, center, 44, 36) > 0.2


def class_selected(frame: np.ndarray, center: Point) -> bool:
    return _ring_gold(frame, center, 38, 32) > 0.2


def class_available(frame: np.ndarray, center: Point) -> bool:
    return mean_saturation(frame, center, 25, 25) > 40


def gender_selected(frame: np.ndarray, center: Point) -> bool:
    return _ring_gold(frame, center, 26, 0) > 0.1