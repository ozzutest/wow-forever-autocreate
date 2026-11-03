from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, ValidationInfo, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Faction(str, Enum):
    HORDE = "horde"
    ALLIANCE = "alliance"


class Gender(str, Enum):
    MALE = "male"
    FEMALE = "female"


class ModelType(str, Enum):
    HD = "hd"
    SD = "sd"


class CharClass(str, Enum):
    WARRIOR = "warrior"
    PALADIN = "paladin"
    HUNTER = "hunter"
    ROGUE = "rogue"
    PRIEST = "priest"
    SHAMAN = "shaman"
    MAGE = "mage"
    WARLOCK = "warlock"
    DRUID = "druid"


# ---------------------------------------------------------------- game data (races.yaml)


class NamePartRule(StrictModel):
    min_len: PositiveInt
    max_len: PositiveInt
    max_repeat: PositiveInt = 2  # "can't use the same letter three times in a row"

    def check(self, value: str, label: str) -> None:
        if not self.min_len <= len(value) <= self.max_len:
            raise ValueError(f"{label} {value!r}: length must be {self.min_len}-{self.max_len}")
        run = self.max_repeat + 1
        if re.search(rf"(.)\1{{{run - 1}}}", value, re.IGNORECASE):
            raise ValueError(f"{label} {value!r}: same letter {run} times in a row")


class NameRules(StrictModel):
    main: NamePartRule
    secondary: NamePartRule


class GenderAppearance(StrictModel):
    # Ordered tabs, each an ordered mapping of field -> number of options.
    # Order = position on screen, which is how the bot finds tab icons and rows.
    tabs: list[dict[str, PositiveInt]] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_fields(self) -> GenderAppearance:
        seen: set[str] = set()
        for tab in self.tabs:
            dup = seen & tab.keys()
            if dup:
                raise ValueError(f"field(s) appear in more than one tab: {sorted(dup)}")
            seen |= tab.keys()
        return self

    def locate(self, field: str) -> tuple[int, int, int] | None:
        """Return (tab_index, row_index, option_count), or None if the field doesn't exist."""
        for t, tab in enumerate(self.tabs):
            for r, (name, count) in enumerate(tab.items()):
                if name == field:
                    return t, r, count
        return None


class Race(StrictModel):
    faction: Faction
    requires_pack: bool = False
    classes: frozenset[CharClass] = Field(min_length=1)
    # model type -> gender -> tabs. HD and SD models have different fields.
    # A combination may be missing while its data isn't collected; only "random" works then.
    appearance: dict[ModelType, dict[Gender, GenderAppearance]] = {}


class GameData(StrictModel):
    name_rules: NameRules
    rulesets: dict[str, str] = Field(min_length=1)
    races: dict[str, Race] = Field(min_length=1)


# ---------------------------------------------------------- user config (characters.yaml)


class NamePair(StrictModel):
    main: str
    secondary: str


class Character(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    race: str
    gender: Gender
    char_class: CharClass = Field(alias="class")
    hd_model: bool = True
    names: list[NamePair] = Field(min_length=1)
    appearance: Literal["random"] | dict[str, PositiveInt] = "random"

    @model_validator(mode="after")
    def _check_against_game(self, info: ValidationInfo) -> Character:
        game: GameData | None = (info.context or {}).get("game")
        if game is None:
            raise ValueError("validate with context={'game': GameData}")

        race = game.races.get(self.race)
        if race is None:
            raise ValueError(f"unknown race {self.race!r}; known: {sorted(game.races)}")
        if self.char_class not in race.classes:
            allowed = sorted(c.value for c in race.classes)
            raise ValueError(f"{self.race} can't be {self.char_class.value}; allowed: {allowed}")

        for pair in self.names:
            game.name_rules.main.check(pair.main, "main name")
            game.name_rules.secondary.check(pair.secondary, "secondary name")
        keys = [(p.main.lower(), p.secondary.lower()) for p in self.names]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate name pairs")

        if isinstance(self.appearance, dict):
            model = ModelType.HD if self.hd_model else ModelType.SD
            looks = race.appearance.get(model, {}).get(self.gender)
            if looks is None:
                raise ValueError(
                    f"no appearance data for {model.value} {self.gender.value} {self.race}; "
                    "use 'random'"
                )
            for field, index in self.appearance.items():
                loc = looks.locate(field)
                if loc is None:
                    known = [f for tab in looks.tabs for f in tab]
                    raise ValueError(f"{self.race} has no field {field!r}; known: {known}")
                if index > loc[2]:
                    raise ValueError(f"{field}={index} out of range (1-{loc[2]})")
        return self


class CharactersFile(StrictModel):
    ruleset: str
    has_pack: bool = False
    characters: list[Character] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_against_game(self, info: ValidationInfo) -> CharactersFile:
        game: GameData = info.context["game"]
        if self.ruleset not in game.rulesets:
            raise ValueError(f"unknown ruleset {self.ruleset!r}; known: {sorted(game.rulesets)}")
        for c in self.characters:
            if game.races[c.race].requires_pack and not self.has_pack:
                raise ValueError(f"{c.race} requires the pack; set has_pack: true")
        return self

    @model_validator(mode="after")
    def _check_pack(self, info: ValidationInfo) -> CharactersFile:
        game: GameData = info.context["game"]
        for c in self.characters:
            if game.races[c.race].requires_pack and not self.has_pack:
                raise ValueError(f"{c.race} requires the pack; set has_pack: true")
        return self


# ---------------------------------------------------------------------------- loaders


def load_game(path: str | Path) -> GameData:
    return GameData.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def load_characters(path: str | Path, game: GameData) -> CharactersFile:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return CharactersFile.model_validate(data, context={"game": game})