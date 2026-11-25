import json

from app.paths import CONFIG


def load() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def save(cfg: dict) -> None:
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
