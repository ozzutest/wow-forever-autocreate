"""State machine tests: the error policy (pure), and handlers on real fixtures with
a fake input that records clicks instead of sending them."""
import random
from pathlib import Path
from types import SimpleNamespace as NS

import cv2
import pytest
import yaml

from states import policy
from states.context import Context, pair_key
from states.handlers import STUCK_AFTER, Machine, Outcome
from vision.classify import classify, State

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures"
LAYOUT = yaml.safe_load((ROOT / "vision" / "layout.yaml").read_text())
TITLES = ["Normal", "PvP", "Roleplay", "Roleplay PvP"]


def pair(m, s):
    return NS(main=m, secondary=s)


def char(*names):
    return NS(race="orc", gender=NS(value="male"), char_class=NS(value="warrior"),
              hd_model=True, names=[pair(*n) for n in names])


# ------------------------------------------------------------------ policy

def test_faction_imbalance_waits_without_limit():
    ctx = Context()
    for _ in range(50):
        policy.on_error(ctx, "faction_imbalance", 1000.0, random.Random(1))
    assert 1030 <= ctx.next_finish_at <= 1060
    assert not ctx.escalate and ctx.fresh_customize is False


def test_throttle_backs_off_then_escalates():
    ctx = Context()
    waits = []
    for _ in range(policy.THROTTLE_ESCALATE_AT):
        policy.on_error(ctx, "throttled", 0.0)
        waits.append(ctx.next_finish_at)
    assert waits[:3] == [10.0, 30.0, 60.0] and waits[-1] == 60.0
    assert ctx.escalate


def test_timed_out_shares_the_throttle_counter():
    ctx = Context()
    policy.on_error(ctx, "throttled", 0.0)
    policy.on_error(ctx, "timed_out", 0.0)
    assert ctx.throttle_count == 2


def test_reconnect_backoff_caps_at_15_min():
    ctx = Context()
    for _ in range(10):
        policy.on_error(ctx, "disconnected", 0.0)
    assert ctx.next_reconnect_at == 900.0


def test_error_clears_finish_clicked():
    ctx = Context(finish_clicked=True)
    policy.on_error(ctx, "unknown", 0.0)
    assert not ctx.finish_clicked and ctx.next_finish_at == policy.UNKNOWN_WAIT


def test_unknown_error_also_slows_down_reconnect():
    ctx = Context()
    policy.on_error(ctx, "unknown", 0.0)
    policy.on_error(ctx, "unknown", 0.0)
    assert ctx.next_reconnect_at == policy.RECONNECT_BACKOFF[1]


def test_remaining_names_skips_rejected_case_insensitively():
    ctx = Context()
    ch = char(("Marek", "Darek"), ("Og", "herbata"))
    ctx.rejected_names.add(pair_key(pair("MAREK", "darek")))
    assert [p.main for p in ctx.remaining_names(ch.names)] == ["Og"]


# ---------------------------------------------------------------- handlers

class FakeInput:
    def __init__(self):
        self.clicks, self.keys = [], []

    def click(self, x, y):
        self.clicks.append((x, y))

    def key(self, *k):
        self.keys.append(k)


class FakeBot:
    """Just enough of slice_orc.Bot: wait() returns a scripted frame."""
    def __init__(self, next_frame=None):
        self.inp = FakeInput()
        self.cu = LAYOUT["char_customize"]
        self._next = next_frame

    def wait(self, what, predicate, timeout):
        assert self._next is not None and predicate(self._next), what
        return self._next

    def frame(self):
        return self._next


def machine(chars, target="PvP", next_frame=None, tmp_path=Path("/tmp/shots")):
    ctx = Context(dry_run=True)
    bot = FakeBot(next_frame)
    return Machine(bot, None, chars, target, TITLES, LAYOUT, ctx, tmp_path), ctx, bot


def run(m, name):
    frame = cv2.imread(str(FIX / name))
    assert frame is not None, f"missing fixture {name}"
    assert frame.shape[:2] == (1080, 1920), f"{name} must be a full 1920x1080 frame, not a crop"
    obs = classify(frame, TITLES)
    return obs, m.handle(obs, frame)


def test_reconnect_clicks_immediately_at_start():
    m, ctx, bot = machine([char(("A", "B"))])
    obs, out = run(m, "reconnect_01.png")
    assert obs.state is State.RECONNECT and out is Outcome.CONTINUE
    assert bot.inp.clicks == [tuple(LAYOUT["reconnect"]["reconnect"])]


def test_reconnect_waits_for_backoff_after_disconnect():
    m, ctx, bot = machine([char(("A", "B"))])
    ctx.next_reconnect_at = 1e12
    run(m, "reconnect_01.png")
    assert bot.inp.clicks == []


def test_escalation_reconnects_without_waiting():
    m, ctx, bot = machine([char(("A", "B"))])
    ctx.escalate, ctx.throttle_count, ctx.next_reconnect_at = True, 6, 1e12
    run(m, "reconnect_01.png")
    assert bot.inp.clicks and not ctx.escalate and ctx.throttle_count == 0


def test_char_select_creates_missing_character():
    m, ctx, bot = machine([char(("Marek", "Darek"))], target="Roleplay PvP")
    _, out = run(m, "char_select_empty_roleplay_pvp_01.png")
    assert out is Outcome.CONTINUE and ctx.current == 0
    assert bot.inp.clicks == [tuple(LAYOUT["char_select"]["create_character"])]


def test_char_select_wrong_ruleset_opens_ruleset_screen():
    m, ctx, bot = machine([char(("Marek", "Darek"))], target="Normal")
    run(m, "char_select_empty_roleplay_pvp_01.png")
    assert bot.inp.clicks == [tuple(LAYOUT["char_select"]["ruleset_button"])]


def test_char_select_done_when_names_are_listed():
    m, ctx, bot = machine([char(("Frost", "Bolte")), char(("Nope", "Nope"), ("Laser", "Tape"))])
    _, out = run(m, "char_select_pvp_01.png")
    assert out is Outcome.DONE and ctx.created == {0, 1} and bot.inp.clicks == []


def test_char_select_after_finish_counts_as_created():
    m, ctx, bot = machine([char(("Marek", "Darek")), char(("Og", "Ziomek"))], target="Roleplay PvP")
    ctx.start_character(0)
    ctx.finish_clicked = True
    run(m, "char_select_empty_roleplay_pvp_01.png")
    assert ctx.created == {0} and ctx.current == 1


def test_char_select_escalation_goes_back():
    m, ctx, bot = machine([char(("A", "B"))])
    ctx.escalate = True
    run(m, "char_select_pvp_01.png")
    (x, y), = bot.inp.clicks
    assert abs(x - 105) < 30 and abs(y - 1047) < 15


def test_ruleset_select_clicks_card_then_select():
    ready = cv2.imread(str(FIX / "ruleset_select_pvp_01.png"))   # Select is red here
    m, ctx, bot = machine([char(("A", "B"))], target="PvP", next_frame=ready)
    obs, _ = run(m, "ruleset_select_01.png")                      # nothing selected yet
    icon, select = bot.inp.clicks
    assert abs(icon[0] - 816) < 20 and abs(icon[1] - 447) < 15   # PvP card icon
    assert select == tuple(LAYOUT["ruleset_select"]["select"])


# ------------------------------------------------- ruleset unavailable: refresh

UNAVAILABLE = "ruleset_select_unavailable_normal_01.png"   # Normal greyed, Roleplay selected


def test_unavailable_ruleset_waits_first():
    m, ctx, bot = machine([char(("A", "B"))], target="Normal")
    run(m, UNAVAILABLE)
    assert bot.inp.clicks == [] and ctx.ruleset_wait_since is not None


def test_unavailable_ruleset_refreshes_with_cancel():
    m, ctx, bot = machine([char(("A", "B"))], target="Normal")
    ctx.ruleset_wait_since = 1.0          # waited "forever"
    run(m, UNAVAILABLE)
    assert bot.inp.clicks == [tuple(LAYOUT["ruleset_select"]["cancel"])]
    assert ctx.ruleset_refreshes == 1 and not ctx.escalate


def test_unavailable_ruleset_escalates_after_refreshes():
    m, ctx, bot = machine([char(("A", "B"))], target="Normal")
    ctx.ruleset_wait_since, ctx.ruleset_refreshes = 1.0, policy.RULESET_RECONNECT_AFTER - 1
    run(m, UNAVAILABLE)
    assert ctx.escalate and ctx.ruleset_refreshes == 0


def test_available_ruleset_on_same_screen_is_selected():
    ready = cv2.imread(str(FIX / "ruleset_select_roleplay_pvp_01.png"))  # Select red
    m, ctx, bot = machine([char(("A", "B"))], target="Roleplay PvP", next_frame=ready)
    ctx.ruleset_refreshes = 3
    run(m, UNAVAILABLE)
    icon, select = bot.inp.clicks
    assert abs(icon[0] - 1380) < 20 and select == tuple(LAYOUT["ruleset_select"]["select"])
    assert ctx.ruleset_refreshes == 0


# --------------------------------------- char select without a ruleset title

NO_TITLE = "char_select_no_title_01.png"   # live: title gone after Cancel on the ruleset screen


def test_no_title_waits_a_few_frames():
    m, ctx, bot = machine([char(("Marek", "Darek"))], target="Normal")
    run(m, NO_TITLE)
    assert bot.inp.clicks == []


def test_no_title_checks_ruleset_instead_of_creating():
    m, ctx, bot = machine([char(("Marek", "Darek"))], target="Normal")
    ctx.unreadable_ruleset = 99
    run(m, NO_TITLE)
    assert bot.inp.clicks == [tuple(LAYOUT["char_select"]["ruleset_button"])]
    assert ctx.current is None                     # nothing started


def test_no_title_trusted_right_after_we_selected_it():
    m, ctx, bot = machine([char(("Marek", "Darek"))], target="Normal")
    ctx.ruleset_confirmed = True
    run(m, NO_TITLE)
    assert bot.inp.clicks == [tuple(LAYOUT["char_select"]["create_character"])]


def test_cancel_on_ruleset_screen_clears_confirmation():
    m, ctx, bot = machine([char(("A", "B"))], target="Normal")
    ctx.ruleset_wait_since, ctx.ruleset_confirmed = 1.0, True
    run(m, UNAVAILABLE)
    assert not ctx.ruleset_confirmed


def test_selecting_target_confirms_it():
    ready = cv2.imread(str(FIX / "ruleset_select_pvp_01.png"))
    m, ctx, bot = machine([char(("A", "B"))], target="PvP", next_frame=ready)
    run(m, "ruleset_select_01.png")
    assert ctx.ruleset_confirmed


# ------------------------------------------------------- fatal and rehearsal

FATAL = "fatal_max_characters_01.png"   # live: popup after Finish on a full account


def test_fatal_stops_and_clicks_okay():
    m, ctx, bot = machine([char(("A", "B"))])
    obs, out = run(m, FATAL)
    assert out is Outcome.STOP
    (x, y), = bot.inp.clicks
    assert abs(x - 958) < 20 and abs(y - 575) < 12


def test_rehearsal_handles_fatal_as_throttle():
    m, ctx, bot = machine([char(("A", "B"))])
    ctx.rehearse_fatal_as, ctx.finish_clicked = "throttled", True
    obs, out = run(m, FATAL)
    assert out is Outcome.CONTINUE and len(bot.inp.clicks) == 1
    assert ctx.throttle_count == 1 and ctx.last_error == "throttled"
    assert not ctx.finish_clicked and not ctx.fresh_customize   # retry with a fresh name check


# ------------------------------------------------------------ stuck watchdog

def test_repeated_clicks_without_progress_escalate(tmp_path):
    m, ctx, bot = machine([char(("Marek", "Darek"))], target="Normal", tmp_path=tmp_path)
    ctx.unreadable_ruleset = 99
    for _ in range(STUCK_AFTER + 1):
        ctx.cooldown_until = 0.0                 # pretend the cooldown passed
        ctx.unreadable_ruleset = 99
        run(m, NO_TITLE)                         # RULESET clicked, screen never changes
    assert len(bot.inp.clicks) == STUCK_AFTER    # the extra click was replaced by escalation
    assert ctx.escalate
    assert list(tmp_path.glob("*_stuck_char_select.png"))


def test_screen_change_resets_the_watchdog(tmp_path):
    m, ctx, bot = machine([char(("Marek", "Darek"))], target="Normal", tmp_path=tmp_path)
    for i in range(10):
        ctx.cooldown_until, ctx.unreadable_ruleset = 0.0, 99
        run(m, NO_TITLE if i % 2 == 0 else "reconnect_01.png")   # alternating screens = progress
    assert not ctx.escalate


def test_stuck_dialog_gets_enter(tmp_path):
    m, ctx, bot = machine([char(("A", "B"))], tmp_path=tmp_path)
    ctx.rehearse_fatal_as = "faction_imbalance"
    for _ in range(STUCK_AFTER + 1):
        ctx.cooldown_until = 0.0
        run(m, FATAL)                            # the dialog never goes away
    assert bot.inp.keys == [("Return",)] and not ctx.escalate


# ------------------------------------------------- disconnect (cable unplugged)

def test_two_button_dialog_clicks_okay_not_help():
    m, ctx, bot = machine([char(("A", "B"))])
    obs, _ = run(m, "error_dialog_disconnected_help_01.png")
    assert obs.dialog.known == "disconnected" and obs.dialog.error_code == "WOW51900328"
    (x, y), = bot.inp.clicks
    assert abs(x - 1087) < 25, "clicked Help instead of Okay"


def test_okay_fallback_picks_rightmost_button():
    from vision import ocr
    frame = cv2.imread(str(FIX / "error_dialog_disconnected_help_01.png"))
    ocr_okay, ocr.OKAY_LABEL = ocr.OKAY_LABEL, "label-never-read"   # force the fallback
    try:
        d = ocr.read_dialog(frame)
    finally:
        ocr.OKAY_LABEL = ocr_okay
    assert d.okay is not None and abs(d.okay[0] - 1087) < 25, f"fallback picked {d.okay}"
    assert abs(d.okay[1] - 575) < 12, "picked a button outside the dialog (Log Out?)"


def test_highlighted_reconnect_button_still_recognized_and_clicked():
    m, ctx, bot = machine([char(("A", "B"))])
    obs, _ = run(m, "reconnect_after_disconnect_01.png")   # white "Reconnect" label
    assert obs.state is State.RECONNECT
    assert bot.inp.clicks == [tuple(LAYOUT["reconnect"]["reconnect"])]


def test_reconnect_waits_while_button_inactive():
    import numpy as np
    m, ctx, bot = machine([char(("A", "B"))])
    m.on_reconnect(None, np.zeros((1080, 1920, 3), np.uint8))
    assert bot.inp.clicks == []


def test_blz_error_code_is_parsed():
    import re
    from vision.ocr import ERROR_CODE_RE, ERROR_CODES
    code = re.search(ERROR_CODE_RE, "you have been disconnected blz51901016").group(0).upper()
    assert ERROR_CODES[code] == "disconnected"
