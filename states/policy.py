"""What each error dialog means for the next steps. Pure: no clicks, no I/O.

Every value here is a launch-night knob; change it here, nowhere else.
"""
from __future__ import annotations

import random

from states.context import Context

FACTION_WAIT = (30.0, 60.0)          # s, random in range, no limit
THROTTLE_BACKOFF = (10.0, 30.0, 60.0)
THROTTLE_ESCALATE_AT = 6             # throttled/timed_out in a row -> Back to Reconnect
RECONNECT_BACKOFF = (60.0, 120.0, 300.0, 600.0, 900.0)  # then 900 s forever
UNKNOWN_WAIT = 60.0
RULESET_REFRESH_AFTER = 120.0        # s of "unavailable" before Cancel + RULESET (fresh screen)
RULESET_RECONNECT_AFTER = 5          # refreshes without luck -> Back to Reconnect

RECONNECT_ERRORS = ("disconnected", "no_realms")
# Errors a full account's "maximum number of characters" popup can stand in for (--rehearse-error).
REHEARSABLE = ("faction_imbalance", "throttled", "timed_out", "unknown")
THROTTLE_ERRORS = ("throttled", "timed_out")


def schedule_reconnect(ctx: Context, now: float) -> None:
    ctx.next_reconnect_at = now + RECONNECT_BACKOFF[min(ctx.reconnect_step, len(RECONNECT_BACKOFF) - 1)]
    ctx.reconnect_step += 1


def on_error(ctx: Context, key: str, now: float, rng: random.Random | None = None) -> None:
    """Update Context for an error dialog (key from KNOWN_ERRORS, or 'unknown')."""
    rng = rng or random
    ctx.last_error = key
    ctx.finish_clicked = False
    ctx.fresh_customize = False      # the name gets checked again before the next Finish

    if key == "faction_imbalance":
        ctx.next_finish_at = now + rng.uniform(*FACTION_WAIT)
    elif key in THROTTLE_ERRORS:
        ctx.throttle_count += 1
        ctx.next_finish_at = now + THROTTLE_BACKOFF[min(ctx.throttle_count - 1, len(THROTTLE_BACKOFF) - 1)]
        if ctx.throttle_count >= THROTTLE_ESCALATE_AT:
            ctx.escalate = True
    elif key in RECONNECT_ERRORS:
        schedule_reconnect(ctx, now)
    elif key == "name_rule":
        if ctx.typed_pair:
            ctx.rejected_names.add(ctx.typed_pair)
        ctx.typed_pair = None
    else:
        # Unknown: don't know where Okay leads, so slow down both paths. Without the
        # reconnect backoff, an unknown error at login (cable unplugged, server down)
        # would make the bot click Reconnect every few seconds.
        ctx.next_finish_at = now + UNKNOWN_WAIT
        schedule_reconnect(ctx, now)
