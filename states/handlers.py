"""One handler per screen. Each call looks at one frame, does at most one step,
and returns; the runner grabs a fresh frame and calls again. Nothing here blocks
for long, so a disconnect is noticed within a frame or two whatever we were doing.

Create/customize actions reuse the calibrated Bot from scripts/slice_orc.py.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from enum import Enum
from pathlib import Path

import cv2

from scripts.slice_orc import Bot, SliceError
from states import policy
from states.context import Context, pair_key
from vision.classify import BOTTOM_BAND, Observation, State
from vision.detect import NameStatus, button_active, name_status
from vision.ocr import normalize, read_lines, read_rulesets

log = logging.getLogger("bot")

# ---- timings (seconds)
COOLDOWN = 5.0            # after a screen-changing click, ignore the old screen this long
NAME_SETTLE = 2.0         # the indicator may still show the previous result right after a change
NAME_TIMEOUT = 15.0       # no green/red this long after typing: type again
FINISH_INACTIVE_MAX = 5.0 # green name but grey Finish this long: Back and do a full round
UNKNOWN_SHOT_EVERY = 30.0
RULESET_UNREADABLE_MAX = 3  # frames without a ruleset title above the list before RULESET

CHAR_LIST_BOX = (1555, 150, 1865, 920)  # names in the char select list


class Outcome(Enum):
    CONTINUE = "continue"
    DONE = "done"     # all characters exist (or the dry run reached Finish)
    STOP = "stop"     # can't continue without a human


class Machine:
    def __init__(self, bot: Bot, game, characters: list, ruleset_title: str,
                 titles: list[str], layout: dict, ctx: Context, shots: Path) -> None:
        self.bot = bot
        self.game = game
        self.chars = characters
        self.target = ruleset_title
        self.titles = titles
        self.L = layout
        self.ctx = ctx
        self.shots = shots
        self._last_unknown_shot = 0.0
        self._last_wait_log = 0.0
        self._last_queue_pos: int | None = None

    # ------------------------------------------------------------- plumbing

    def handle(self, obs: Observation, frame) -> Outcome:
        c = self.ctx
        if obs.state.value == c.cooldown_state and time.time() < c.cooldown_until:
            return Outcome.CONTINUE
        return getattr(self, f"on_{obs.state.value}")(obs, frame)

    def cooldown(self, state: State, seconds: float = COOLDOWN) -> None:
        self.ctx.cooldown_state, self.ctx.cooldown_until = state.value, time.time() + seconds

    def click(self, point) -> None:
        self.bot.inp.click(int(point[0]), int(point[1]))

    def save(self, frame, tag: str) -> Path:
        self.shots.mkdir(parents=True, exist_ok=True)
        path = self.shots / f"{datetime.now():%Y%m%d_%H%M%S_%f}_{tag}.png"
        cv2.imwrite(str(path), frame)
        return path

    def wait_log(self, msg: str, *args, every: float = 30.0) -> None:
        """Log a waiting message at most every `every` seconds."""
        now = time.time()
        if now - self._last_wait_log >= every:
            self._last_wait_log = now
            log.info(msg, *args)

    def back(self, frame, state: State, why: str) -> Outcome:
        """Click Back, found by its label; layout position as a fallback."""
        point = next((w.center for l in read_lines(frame, BOTTOM_BAND) for w in l.words
                      if normalize(w.text) == "back"), tuple(self.L["char_select"]["back"]))
        log.info("Back (%s)", why)
        self.click(point)
        self.cooldown(state)
        return Outcome.CONTINUE

    def char_and_looks(self, index: int):
        from config.models import ModelType  # local import keeps tests free of config
        char = self.chars[index]
        model = ModelType.HD if char.hd_model else ModelType.SD
        return char, self.game.races[char.race].appearance[model][char.gender]

    def label(self, index: int) -> str:
        ch = self.chars[index]
        return f"#{index} {ch.gender.value} {ch.race} {ch.char_class.value}"

    def first_pending(self) -> int | None:
        return next((i for i in range(len(self.chars)) if i not in self.ctx.created), None)

    # -------------------------------------------------------- login screens

    def on_logging_in(self, obs, frame) -> Outcome:
        return Outcome.CONTINUE

    def on_queue(self, obs, frame) -> Outcome:
        pos = obs.dialog.queue_position if obs.dialog else None
        if pos != self._last_queue_pos:
            self._last_queue_pos = pos
            self.wait_log("queue position %s", pos, every=60.0)
        return Outcome.CONTINUE

    def on_reconnect(self, obs, frame) -> Outcome:
        c, now = self.ctx, time.time()
        if c.escalate:
            log.info("escalation reached Reconnect: fresh session")
            c.escalate, c.throttle_count, c.next_reconnect_at = False, 0, 0.0
            c.ruleset_refreshes, c.ruleset_wait_since = 0, None
        if now < c.next_reconnect_at:
            self.wait_log("Reconnect in %.0f s", c.next_reconnect_at - now)
            return Outcome.CONTINUE
        c.lost_client_state()
        log.info("clicking Reconnect")
        self.click(self.L["reconnect"]["reconnect"])
        self.cooldown(State.RECONNECT, 10.0)
        return Outcome.CONTINUE

    def on_login_form(self, obs, frame) -> Outcome:
        log.error("login form: the session is gone, log in by hand (screenshot: %s)",
                  self.save(frame, "login_form"))
        return Outcome.STOP

    # -------------------------------------------------------------- dialogs

    def _okay(self, obs) -> None:
        if obs.dialog and obs.dialog.okay:
            self.click(obs.dialog.okay)
        else:
            log.warning("no Okay position found, pressing Enter")
            self.bot.inp.key("Return")

    def on_error(self, obs, frame) -> Outcome:
        return self._error(obs, frame, obs.dialog.known or "unknown")

    def _error(self, obs, frame, key: str) -> Outcome:
        d = obs.dialog
        shot = self.save(frame, f"error_{key}")
        log.warning("error dialog: %s [%s] %r (%s)", key, d.error_code or "-", d.message, shot.name)
        policy.on_error(self.ctx, key, time.time())
        if key in policy.THROTTLE_ERRORS:
            log.info("  throttle count %d/%d%s", self.ctx.throttle_count, policy.THROTTLE_ESCALATE_AT,
                     " -> escalating" if self.ctx.escalate else "")
        self._okay(obs)
        self.cooldown(State.ERROR, 3.0)
        return Outcome.CONTINUE

    def on_fatal(self, obs, frame) -> Outcome:
        if self.ctx.rehearse_fatal_as:
            log.warning("REHEARSAL: handling %r as %s", obs.dialog.message, self.ctx.rehearse_fatal_as)
            return self._error(obs, frame, self.ctx.rehearse_fatal_as)
        log.error("fatal: %r (screenshot: %s)", obs.dialog.message, self.save(frame, "fatal"))
        self._okay(obs)
        return Outcome.STOP

    # ---------------------------------------------------------- ruleset select

    def on_ruleset_select(self, obs, frame) -> Outcome:
        cards = read_rulesets(frame, self.titles)
        card = next((c for c in cards if c.title == self.target), None)
        if card is None:
            self.wait_log("ruleset %r not found on screen (read: %s)", self.target,
                          [c.read_as for c in cards])
            return Outcome.CONTINUE
        rs = self.L["ruleset_select"]
        c, now = self.ctx, time.time()
        if not card.available:
            # The screen may not update by itself: refresh it now and then.
            c.ruleset_wait_since = c.ruleset_wait_since or now
            waited = now - c.ruleset_wait_since
            if waited < policy.RULESET_REFRESH_AFTER:
                self.wait_log("ruleset %r unavailable, refresh in %.0f s", self.target,
                              policy.RULESET_REFRESH_AFTER - waited)
                self.cooldown(State.RULESET_SELECT, 10.0)
                return Outcome.CONTINUE
            c.ruleset_wait_since = None
            c.ruleset_refreshes += 1
            if c.ruleset_refreshes >= policy.RULESET_RECONNECT_AFTER:
                log.info("ruleset %r still unavailable after %d refreshes: reconnecting",
                         self.target, c.ruleset_refreshes)
                c.ruleset_refreshes, c.escalate = 0, True
            else:
                log.info("ruleset %r still unavailable: Cancel to refresh (%d/%d)", self.target,
                         c.ruleset_refreshes, policy.RULESET_RECONNECT_AFTER)
            c.ruleset_confirmed = False
            self.click(rs["cancel"])
            self.cooldown(State.RULESET_SELECT)
            return Outcome.CONTINUE
        c.ruleset_wait_since, c.ruleset_refreshes = None, 0
        log.info("ruleset -> %s", self.target)
        self.click((card.center[0], card.center[1] + rs["icon_offset_y"]))
        try:
            self.bot.wait("Select active", lambda f: button_active(f, tuple(rs["select"])), 5)
        except SliceError as e:
            log.warning("%s", e)
            return Outcome.CONTINUE
        self.click(rs["select"])
        c.ruleset_confirmed = True
        self.cooldown(State.RULESET_SELECT)
        return Outcome.CONTINUE

    # ------------------------------------------------------------ char select

    def listed_names(self, frame) -> set[str]:
        return {normalize(l.text) for l in read_lines(frame, CHAR_LIST_BOX)}

    def on_char_select(self, obs, frame) -> Outcome:
        c = self.ctx
        c.reconnect_step = 0
        if c.escalate:
            return self.back(frame, State.CHAR_SELECT, "escalation: to Reconnect")

        if obs.ruleset is None:
            # The title is missing after Cancel on the ruleset screen (seen live), and the
            # list may then belong to another ruleset. Never create blind: check via RULESET,
            # unless we selected the target ourselves a moment ago.
            if not c.ruleset_confirmed:
                c.unreadable_ruleset += 1
                if c.unreadable_ruleset < RULESET_UNREADABLE_MAX:
                    return Outcome.CONTINUE
                c.unreadable_ruleset = 0
                log.info("no ruleset title above the list: RULESET to check")
                self.click(self.L["char_select"]["ruleset_button"])
                self.cooldown(State.CHAR_SELECT)
                return Outcome.CONTINUE
            self.wait_log("no ruleset title above the list, but we just selected %r", self.target)
        else:
            c.unreadable_ruleset = 0
            if obs.ruleset.title != self.target:
                c.ruleset_confirmed = False
                log.info("char select shows %r, want %r: RULESET", obs.ruleset.title, self.target)
                self.click(self.L["char_select"]["ruleset_button"])
                self.cooldown(State.CHAR_SELECT)
                return Outcome.CONTINUE

        listed = self.listed_names(frame)
        if c.finish_clicked and c.current is not None:
            # Finish -> char select with no error in between means it worked.
            ch = self.chars[c.current]
            seen = any(normalize(f"{p.main} {p.secondary}") in listed for p in ch.names)
            log.info("CREATED %s%s", self.label(c.current), "" if seen else " (name not read in the list)")
            c.created.add(c.current)
            c.finish_clicked, c.throttle_count = False, 0
        for i, ch in enumerate(self.chars):
            if i not in c.created and any(normalize(f"{p.main} {p.secondary}") in listed for p in ch.names):
                log.info("already in the list: %s", self.label(i))
                c.created.add(i)

        nxt = self.first_pending()
        if nxt is None:
            log.info("all %d characters exist", len(self.chars))
            return Outcome.DONE
        if c.current != nxt:
            c.start_character(nxt)
            log.info("next character: %s", self.label(nxt))
        log.info("Create Character")
        self.click(self.L["char_select"]["create_character"])
        self.cooldown(State.CHAR_SELECT)
        return Outcome.CONTINUE

    # ------------------------------------------------------------ char create

    def on_char_create(self, obs, frame) -> Outcome:
        c = self.ctx
        if c.escalate:
            return self.back(frame, State.CHAR_CREATE, "escalation")
        if c.current is None:
            nxt = self.first_pending()
            if nxt is None:
                return Outcome.DONE
            c.start_character(nxt)
            log.info("started on char create: %s", self.label(nxt))
        char, looks = self.char_and_looks(c.current)
        b = self.bot
        try:
            b.select_race(char.race)
            b.select_gender(char.gender.value)
            b.check_classes(self.game, char.race)
            b.select_class(char.char_class.value)
            b.set_hd(char.hd_model)
            b.open_customize(looks)
        except SliceError as e:
            log.warning("char create: %s (screenshot: %s)", e, self.save(b.frame(), "char_create_fail").name)
            self.cooldown(State.CHAR_CREATE)
            return Outcome.CONTINUE
        c.fresh_customize, c.customize_entered_at = True, time.time()
        return Outcome.CONTINUE

    # --------------------------------------------------------- char customize

    def type_pair(self, pair) -> None:
        cu = self.bot.cu
        for point in (cu["name_main"], cu["name_secondary"]):
            self.click(point)
            self.bot.inp.clear_field()
        self.bot.wait("name fields cleared",
                      lambda f: name_status(f, tuple(cu["name_indicator"])) == NameStatus.PENDING, 5)
        self.click(cu["name_main"])
        self.bot.inp.type_text(pair.main)
        self.click(cu["name_secondary"])
        self.bot.inp.type_text(pair.secondary)

    def on_char_customize(self, obs, frame) -> Outcome:
        c, now = self.ctx, time.time()
        if c.escalate:
            return self.back(frame, State.CHAR_CUSTOMIZE, "escalation")
        if c.current is None:
            nxt = self.first_pending()
            if nxt is None:
                return Outcome.DONE
            c.start_character(nxt)  # fresh_customize is False: the round trip sets everything
            log.info("started on customize: %s", self.label(nxt))
        if now < c.next_finish_at:
            self.wait_log("backoff after %s: next Finish in %.0f s", c.last_error, c.next_finish_at - now)
            return Outcome.CONTINUE
        if not c.fresh_customize:
            # Back -> char create -> Customize makes the client check the name again.
            return self.back(frame, State.CHAR_CUSTOMIZE, "revalidate the name")

        char, looks = self.char_and_looks(c.current)
        if not c.appearance_done:
            try:
                self.bot.apply_appearance(char, looks)
            except SliceError as e:
                log.warning("appearance: %s (screenshot: %s)", e, self.save(self.bot.frame(), "appearance_fail").name)
                c.fresh_customize = False  # retry via the round trip
                return Outcome.CONTINUE
            c.appearance_done = True
            return Outcome.CONTINUE

        if c.typed_pair is None:
            remaining = c.remaining_names(char.names)
            if not remaining:
                log.error("all names rejected for %s", self.label(c.current))
                return Outcome.STOP
            pair = remaining[0]
            log.info("name -> %s %s (%d left after this)", pair.main, pair.secondary, len(remaining) - 1)
            try:
                self.type_pair(pair)
            except SliceError as e:
                log.warning("%s", e)
                return Outcome.CONTINUE
            c.typed_pair, c.name_typed_at = pair_key(pair), time.time()
            return Outcome.CONTINUE

        since = now - max(c.customize_entered_at, c.name_typed_at)
        if since < NAME_SETTLE:
            return Outcome.CONTINUE
        status = name_status(frame, tuple(self.bot.cu["name_indicator"]))
        if status is NameStatus.PENDING:
            if since > NAME_TIMEOUT:
                log.warning("no name result after %.0f s: typing again", since)
                c.typed_pair = None
            return Outcome.CONTINUE
        if status is NameStatus.BAD:
            log.info("  name rejected: %s %s", *c.typed_pair)
            c.rejected_names.add(c.typed_pair)
            c.typed_pair = None
            return Outcome.CONTINUE

        finish = tuple(self.bot.cu["finish"])
        if not button_active(frame, finish):
            c.finish_inactive_since = c.finish_inactive_since or now
            if now - c.finish_inactive_since > FINISH_INACTIVE_MAX:
                c.finish_inactive_since, c.fresh_customize = None, False
                return self.back(frame, State.CHAR_CUSTOMIZE, "Finish stays disabled")
            return Outcome.CONTINUE
        c.finish_inactive_since = None

        if c.dry_run:
            log.info("DRY RUN: name %s %s accepted, Finish active. Stopping before Finish.", *c.typed_pair)
            self.save(frame, "dry_run_ready")
            return Outcome.DONE
        c.finish_attempts += 1
        log.info("Finish (attempt %d)", c.finish_attempts)
        self.click(finish)
        c.finish_clicked, c.fresh_customize = True, False
        self.cooldown(State.CHAR_CUSTOMIZE)
        return Outcome.CONTINUE

    # -------------------------------------------------------------- unknown

    def on_unknown(self, obs, frame) -> Outcome:
        now = time.time()
        if now - self._last_unknown_shot >= UNKNOWN_SHOT_EVERY:
            self._last_unknown_shot = now
            log.info("unknown screen (%r), screenshot %s", obs.text[:80], self.save(frame, "unknown").name)
        return Outcome.CONTINUE
