import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GAME = json.loads((ROOT / "project.json").read_text(encoding="utf-8"))
if GAME.get("schema") != 1 or GAME.get("mode") != "psp-donor":
    raise ValueError("Invalid project configuration")
if not str(GAME.get("app_id", "")).isdigit():
    raise ValueError("Invalid Steam app ID")
