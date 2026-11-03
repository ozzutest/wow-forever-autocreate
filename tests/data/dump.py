import yaml
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent.parent / "config"
game = yaml.safe_load(open(CONFIG / "races.yaml"))

chars = []
for race, r in game["races"].items():
    for model, genders in r["appearance"].items():
        for gender, g in genders.items():
            chars.append({"race": race, "gender": gender, "class": r["classes"][0], "hd_model": model == "hd",
                          "names": [{"main": "Probex", "secondary": "Testa"}],
                          "appearance": {k: v for tab in g["tabs"] for k, v in tab.items()}})

with open('./probe_all.yaml', 'w') as out:
    yaml.dump(chars, out, default_flow_style=False)