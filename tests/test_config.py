import pytest
from pathlib import Path
from config import load_characters, load_game, CharactersFile
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]


def test_example_config_loads():
    game = load_game(ROOT / "config" / "races.yaml")
    chars = load_characters(ROOT / "characters.example.yaml", game)
    assert len(chars.characters) == 2
    

def test_unknown_ruleset_rejected():
    game = load_game(ROOT / "config" / "races.yaml")
    data = {"ruleset": "pve", "characters": [{"race": "orc", "gender": "male", "class": "warrior",
            "names": [{"main": "Og", "secondary": "Herbata"}]}]}
    with pytest.raises(ValidationError, match="unknown ruleset"):
        CharactersFile.model_validate(data, context={"game": game})

BASE = {"race": "orc", "gender": "male", "class": "warrior",
        "names": [{"main": "Og", "secondary": "Herbata"}]}

@pytest.mark.parametrize("override, error", [
    ({"class": "paladin"}, "can't be paladin"),
    ({"race": "murloc"}, "unknown race"),
    ({"names": [{"main": "Wwwlad", "secondary": "Ok"}]}, "3 times in a row"),
    ({"names": [{"main": "O", "secondary": "Ok"}]}, "length must be"),
    ({"names": [{"main": "Og", "secondary": "Ab"}] * 2}, "duplicate name pairs"),
    ({"appearance": {"piercings": 2}}, "has no field 'piercings'"),
    ({"appearance": {"grime": 4}}, "out of range"),
    ({"hd_model": False, "appearance": {"grime": 2}}, "has no field 'grime'"),
    ({"apperance": "random"}, "Extra inputs"),
])

def test_invalid_character_rejected(override, error):
    game = load_game(ROOT / "config" / "races.yaml")
    data = {"ruleset": "pvp", "characters": [BASE | override]}
    with pytest.raises(ValidationError, match=error):
        CharactersFile.model_validate(data, context={"game": game})

def test_skyborne_needs_pack():
    game = load_game(ROOT / "config" / "races.yaml")
    data = {"ruleset": "pvp", "has_pack": False,
            "characters": [BASE | {"race": "skyborne_elf_horde"}]}
    with pytest.raises(ValidationError, match="skyborne_elf_horde requires the pack; set has_pack: true"):  
        CharactersFile.model_validate(data, context={"game": game})