"""Show which appearance fields a race has, and how many options each one has.

Usage:
    python -m scripts.list_options                 # all races
    python -m scripts.list_options undead          # one race
    python -m scripts.list_options undead female   # one race and gender

Use the field names and numbers shown here in characters.yaml, e.g.
    appearance:
      hair_style: 4      # option 4 of the list in the game, counting from 1
"""
from __future__ import annotations

import sys
from pathlib import Path

from config import load_game

ROOT = Path(__file__).resolve().parents[1]


def _v(x) -> str:
    return str(getattr(x, "value", x))


def main(argv: list[str]) -> int:
    game = load_game(ROOT / "config" / "races.yaml")
    race_filter = argv[0] if argv else None
    gender_filter = argv[1] if len(argv) > 1 else None
    if race_filter and race_filter not in game.races:
        print(f"unknown race {race_filter!r}; races: {', '.join(game.races)}")
        return 2
    for race, data in game.races.items():
        if race_filter and race != race_filter:
            continue
        classes = ", ".join(_v(c) for c in data.classes)
        print(f"\n{race}  (classes: {classes})")
        for model, by_gender in data.appearance.items():
            for gender, looks in by_gender.items():
                if gender_filter and _v(gender) != gender_filter:
                    continue
                print(f"  {_v(model)} {_v(gender)}:")
                for t, tab in enumerate(looks.tabs, 1):
                    fields = ", ".join(f"{f} (1-{n})" for f, n in tab.items())
                    print(f"    tab {t}: {fields}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
