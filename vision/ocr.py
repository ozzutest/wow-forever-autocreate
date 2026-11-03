"""OCR for dialogs and other text whose position or content varies.

Text in WoW's glue screens is gold/yellow, so frames are masked to gold pixels
before Tesseract sees them; that removes most of the animated background.
Low-confidence words (background noise) are dropped.

All tunable values live in the CONSTANTS section below; the functions don't
contain magic numbers. Coordinates are window coordinates at 1920x1080.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from enum import Enum

import cv2
import numpy as np
import pytesseract

# =================================================================== CONSTANTS

# ---- Tesseract
SCALE = 2                  # upscaling helps Tesseract with the small UI font
MIN_CONF = 60              # words below this confidence are background noise
MIN_LINE_CHARS = 3         # shorter lines are stray glyphs ("v", "|", "~")
TESS_BLOCK = "--psm 6"     # dialogs: a uniform block of text
TESS_LINE = "--psm 7"      # ruleset titles: a single line

# ---- Gold text mask (HSV, OpenCV ranges: H 0-179, S/V 0-255)
GOLD_H = (15, 40)
GOLD_S_MIN = 90
GOLD_V_MIN = 170

# ---- Red button mask, for finding Okay when OCR misses its label
RED_H_LOW_MAX = 8          # red wraps around hue 0: H <= 8 or H >= 170
RED_H_HIGH_MIN = 170
RED_S_MIN = 150
RED_V_MIN = 80
RED_CLOSE_KERNEL = (5, 15) # joins the label gap inside a button
BUTTON_W = (120, 400)      # calibrated on dialog fixtures
BUTTON_H = (18, 60)
BUTTON_MIN_ASPECT = 3      # buttons are wide: w > 3 * h

# ---- Regions
DIALOG_BOX = (560, 300, 1360, 760)        # center of the frame, where dialogs appear
RULESET_TITLE_BAND = (0, 365, 1920, 400)  # title row of the ruleset cards

# ---- Fuzzy matching
FUZZY_CUTOFF = 0.8         # "imbalanee" vs "imbalance" = 0.89; unrelated words stay far below
TITLE_CUTOFF = 0.6         # ruleset titles are short ("PyP" -> "PvP")

# ---- Dialog messages (normalized: lowercase, no punctuation)
BUSY_MESSAGES = (
    "connecting",
    "retrieving server information",
    "logging in to game server",
    "retrieving character list",   # over char select, can linger after the list appears
)
QUEUE_MESSAGE = "login servers are full"
QUEUE_POSITION_RE = r"position in queue (\d+)"
FATAL_MESSAGES = ("maximum number",)
OKAY_LABEL = "okay"
BUTTON_LABELS = ("okay", "cancel")        # left out of Dialog.message
ERROR_CODE_RE = r"wow\d{5,}"

# Known errors, keyed for the retry policy in states/dialogs.py.
# One distinctive keyword per error: a whole phrase breaks as soon as OCR drops one word.
KNOWN_ERRORS = {
    "faction_imbalance": "imbalance",
    "throttled": "throttled",
    "timed_out": "timed out",
    "no_realms": "no realms",
    "disconnected": "disconnected",
    "name_rule": "same letter",
}
# Backup key: digits OCR more reliably than words.
ERROR_CODES = {
    "WOW51900319": "disconnected",
    "WOW51900309": "no_realms",
}

# ---- Ruleset cards
TITLE_GOLD_S_MIN = 60      # looser than GOLD_*: also catches dim (greyed) titles
TITLE_GOLD_V_MIN = 100
TITLE_WORD_GAP = 40        # max px between words of one title ("Roleplay PvP")
TITLE_MIN_WIDTH = 15       # narrower clusters are noise
TITLE_PAD = 6              # px around a cluster before OCR
TITLE_SCALE = 3
TITLE_BRIGHT_V = 170       # available titles: ~61% of pixels above this; greyed: 0%
TITLE_BRIGHT_MIN = 0.3     # fraction of bright pixels to count as available

# ===================================================================== helpers


@dataclass(frozen=True)
class Word:
    text: str
    center: tuple[int, int]  # frame coordinates, ready to click


@dataclass
class Line:
    text: str
    words: list[Word] = field(default_factory=list)


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


def fuzzy_contains(joined: str, kw: str) -> bool:
    """True if normalized text contains the keyword, tolerating small OCR typos."""
    if kw in joined:
        return True
    words, n = joined.split(), len(kw.split())
    return any(
        difflib.SequenceMatcher(None, " ".join(words[i : i + n]), kw).ratio() >= FUZZY_CUTOFF
        for i in range(len(words) - n + 1)
    )


def _gold_mask(roi: np.ndarray) -> np.ndarray:
    h, s, v = cv2.split(cv2.cvtColor(roi, cv2.COLOR_BGR2HSV))
    mask = ((h >= GOLD_H[0]) & (h <= GOLD_H[1]) & (s > GOLD_S_MIN) & (v > GOLD_V_MIN))
    mask = cv2.resize(mask.astype(np.uint8) * 255, None, fx=SCALE, fy=SCALE,
                      interpolation=cv2.INTER_CUBIC)
    return 255 - mask  # dark text on white


def _ocr(frame: np.ndarray, box: tuple[int, int, int, int] | None):
    x0, y0, x1, y1 = box or (0, 0, frame.shape[1], frame.shape[0])
    data = pytesseract.image_to_data(
        _gold_mask(frame[y0:y1, x0:x1]), config=TESS_BLOCK, output_type=pytesseract.Output.DICT
    )
    return data, (x0, y0)


def _center(data: dict, i: int, origin: tuple[int, int]) -> tuple[int, int]:
    x0, y0 = origin
    return (x0 + (data["left"][i] + data["width"][i] // 2) // SCALE,
            y0 + (data["top"][i] + data["height"][i] // 2) // SCALE)


def _lines(data: dict, origin: tuple[int, int]) -> list[Line]:
    lines: dict[tuple[int, int, int], Line] = {}
    for i, text in enumerate(data["text"]):
        if not text.strip() or float(data["conf"][i]) < MIN_CONF:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        line = lines.setdefault(key, Line(""))
        line.words.append(Word(text, _center(data, i, origin)))
        line.text = " ".join(w.text for w in line.words)
    return [l for l in lines.values() if len(normalize(l.text).replace(" ", "")) >= MIN_LINE_CHARS]


def read_lines(frame: np.ndarray, box: tuple[int, int, int, int] | None = None) -> list[Line]:
    data, origin = _ocr(frame, box)
    return _lines(data, origin)


def red_buttons(frame: np.ndarray, box: tuple[int, int, int, int] | None = None) -> list[tuple[int, int]]:
    """Centers of red buttons (Okay, Cancel, Reconnect...) in the box, top to bottom.

    Fallback for when OCR misses the button label.
    """
    x0, y0, x1, y1 = box or (0, 0, frame.shape[1], frame.shape[0])
    h, s, v = cv2.split(cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV))
    mask = (((h <= RED_H_LOW_MAX) | (h >= RED_H_HIGH_MIN)) & (s > RED_S_MIN) & (v > RED_V_MIN))
    mask = cv2.morphologyEx(mask.astype(np.uint8) * 255, cv2.MORPH_CLOSE,
                            np.ones(RED_CLOSE_KERNEL, np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    found = []
    for c in contours:
        x, y, w, hh = cv2.boundingRect(c)
        if (BUTTON_W[0] <= w <= BUTTON_W[1] and BUTTON_H[0] <= hh <= BUTTON_H[1]
                and w > BUTTON_MIN_ASPECT * hh):
            found.append((x0 + x + w // 2, y0 + y + hh // 2))
    return sorted(found, key=lambda p: p[1])


# ===================================================================== dialogs


class DialogKind(Enum):
    NONE = "none"        # no recognizable dialog
    BUSY = "busy"        # LOGGING_IN: connecting / retrieving / logging in: wait
    QUEUE = "queue"      # login queue: wait, never cancel
    ERROR = "error"      # dialog with Okay: click it, back off, retry
    FATAL = "fatal"      # error that retrying can't fix: stop


@dataclass
class Dialog:
    kind: DialogKind
    message: str = ""
    error_code: str | None = None
    queue_position: int | None = None
    okay: tuple[int, int] | None = None  # where to click "Okay"
    known: str | None = None              # key from KNOWN_ERRORS, if recognized


def match_known(joined: str) -> str | None:
    """Key from KNOWN_ERRORS for normalized dialog text, exact first, then fuzzy."""
    for key, kw in KNOWN_ERRORS.items():
        if kw in joined:
            return key
    return next((key for key, kw in KNOWN_ERRORS.items() if fuzzy_contains(joined, kw)), None)


def read_dialog(frame: np.ndarray, box: tuple[int, int, int, int] | None = DIALOG_BOX) -> Dialog:
    data, origin = _ocr(frame, box)
    lines = _lines(data, origin)
    texts = [normalize(l.text) for l in lines]
    joined = " ".join(texts)

    code = re.search(ERROR_CODE_RE, joined)
    error_code = code.group(0).upper() if code else None
    known = match_known(joined) or ERROR_CODES.get(error_code or "")

    # "Okay" sometimes comes back with low confidence; accept it at any confidence
    okay = next((_center(data, i, origin) for i, t in enumerate(data["text"])
                 if normalize(t) == OKAY_LABEL), None)
    if okay is None and (known or error_code) and lines:
        # label not read: take the first red button below the message
        bottom = max(w.center[1] for l in lines for w in l.words)
        okay = next((b for b in red_buttons(frame, box) if b[1] > bottom), None)
    message = " ".join(l.text for l in lines if normalize(l.text) not in BUTTON_LABELS)

    if any(m in joined for m in FATAL_MESSAGES):
        return Dialog(DialogKind.FATAL, message, error_code, okay=okay)
    if QUEUE_MESSAGE in joined:
        pos = re.search(QUEUE_POSITION_RE, joined)
        return Dialog(DialogKind.QUEUE, message, queue_position=int(pos.group(1)) if pos else None)
    if any(t.startswith(m) for t in texts for m in BUSY_MESSAGES):
        return Dialog(DialogKind.BUSY, message)
    if okay is not None or error_code or known:
        return Dialog(DialogKind.ERROR, message, error_code, okay=okay, known=known)
    return Dialog(DialogKind.NONE, message)


# ==================================================================== rulesets


@dataclass(frozen=True)
class RulesetCard:
    title: str                 # the matched UI title from races.yaml, e.g. "Roleplay PvP"
    read_as: str               # raw OCR text, for the log
    center: tuple[int, int]    # center of the title; the card icon is about 65 px below
    available: bool


def read_rulesets(
    frame: np.ndarray, titles: list[str], band: tuple[int, int, int, int] = RULESET_TITLE_BAND
) -> list[RulesetCard]:
    """Find ruleset titles in a band, left to right.

    Used for the ruleset cards and for the title above the char select list.
    Titles are found as horizontal clusters of gold pixels (works for 3 or 4 cards,
    wherever they're centered), then each cluster is OCR'd on its own, which is far
    more reliable than one pass over the whole row (that missed "PvP" entirely).
    OCR'd text is fuzzy-matched to the known titles ("PyP" -> "PvP").
    Availability comes from title brightness (see TITLE_BRIGHT_*).
    """
    x0, y0, x1, y1 = band
    h, s, v = cv2.split(cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV))
    mask = ((h >= GOLD_H[0]) & (h <= GOLD_H[1])
            & (s > TITLE_GOLD_S_MIN) & (v > TITLE_GOLD_V_MIN))

    cols = np.flatnonzero(mask.any(axis=0))
    clusters: list[list[int]] = []
    for c in cols:
        if clusters and c - clusters[-1][1] <= TITLE_WORD_GAP:
            clusters[-1][1] = c
        else:
            clusters.append([c, c])

    by_norm = {normalize(t): t for t in titles}
    best: dict[str, tuple[float, RulesetCard]] = {}
    for a, b in clusters:
        if b - a <= TITLE_MIN_WIDTH:
            continue
        sub = mask[:, max(a - TITLE_PAD, 0) : b + TITLE_PAD + 1].astype(np.uint8) * 255
        sub = 255 - cv2.resize(sub, None, fx=TITLE_SCALE, fy=TITLE_SCALE,
                               interpolation=cv2.INTER_CUBIC)
        read = pytesseract.image_to_string(sub, config=TESS_LINE).strip()
        match = difflib.get_close_matches(normalize(read), list(by_norm), n=1, cutoff=TITLE_CUTOFF)
        if not match:
            continue
        ratio = difflib.SequenceMatcher(None, normalize(read), match[0]).ratio()
        bright = float((v[:, a : b + 1][mask[:, a : b + 1]] > TITLE_BRIGHT_V).mean())
        ys = np.flatnonzero(mask[:, a : b + 1].any(axis=1))
        card = RulesetCard(
            title=by_norm[match[0]],
            read_as=read,
            center=(x0 + (a + b) // 2, y0 + int(ys.mean())),
            available=bright > TITLE_BRIGHT_MIN,
        )
        if card.title not in best or ratio > best[card.title][0]:
            best[card.title] = (ratio, card)
    return sorted((c for _, c in best.values()), key=lambda c: c.center[0])