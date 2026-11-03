"""Everything the bot remembers between frames.

The client is the source of truth for *where* we are (classify() reads it every
frame); Context only holds what the screen can't tell us: which character we're
on, which names failed, backoffs, and whether we just clicked Finish.
"""
from __future__ import annotations

from dataclasses import dataclass, field

NameKey = tuple[str, str]


def pair_key(pair) -> NameKey:
    """A {main, secondary} pair as a case-insensitive key."""
    return pair.main.lower(), pair.secondary.lower()


@dataclass
class Context:
    dry_run: bool = True
    rehearse_fatal_as: str | None = None       # testing only: handle FATAL as this error key

    # ---- character progress
    current: int | None = None                 # index into characters.yaml
    created: set[int] = field(default_factory=set)
    rejected_names: set[NameKey] = field(default_factory=set)  # red X: skipped forever
    typed_pair: NameKey | None = None          # what's in the name fields right now
    appearance_done: bool = False              # survives customize <-> create, not RECONNECT
    fresh_customize: bool = False              # entered customize from char create since the
                                               # last Finish/error: the client checked the name
    customize_entered_at: float = 0.0
    name_typed_at: float = 0.0
    finish_clicked: bool = False               # Finish clicked and no error since
    finish_inactive_since: float | None = None

    # ---- retry state
    finish_attempts: int = 0
    throttle_count: int = 0                    # throttled + timed_out, shared
    next_finish_at: float = 0.0
    next_reconnect_at: float = 0.0
    reconnect_step: int = 0
    escalate: bool = False                     # walk Back to RECONNECT and reconnect
    last_error: str | None = None

    # ---- pacing: don't repeat a click while the screen is still changing
    cooldown_state: str | None = None
    cooldown_until: float = 0.0
    unreadable_ruleset: int = 0
    ruleset_wait_since: float | None = None    # target ruleset greyed since
    ruleset_refreshes: int = 0                 # Cancel + RULESET cycles without luck
    ruleset_confirmed: bool = False            # we clicked Select on the target ourselves

    def remaining_names(self, names) -> list:
        return [p for p in names if pair_key(p) not in self.rejected_names]

    def start_character(self, index: int) -> None:
        self.current = index
        self.typed_pair = None
        self.appearance_done = False
        self.fresh_customize = False
        self.finish_clicked = False
        self.finish_inactive_since = None

    def lost_client_state(self) -> None:
        """After RECONNECT the client has forgotten appearance and names."""
        self.ruleset_confirmed = False
        self.typed_pair = None
        self.appearance_done = False
        self.fresh_customize = False
        self.finish_clicked = False
        self.finish_inactive_since = None
