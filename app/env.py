# secrets live in .env next to config.json, never in git
from app.paths import ROOT

ENV = ROOT / ".env"
PLACEHOLDER = "ВСТАВ_СЮДИ"


def read() -> dict:
    out = {}
    try:
        for line in ENV.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return out


def has(*keys: str) -> bool:
    e = read()
    return all(e.get(k) and e.get(k) != PLACEHOLDER for k in keys)


def add_template(text: str) -> None:
    # appends the lines once, so the user only has to fill them in
    old = ENV.read_text(encoding="utf-8-sig") if ENV.exists() else ""
    first = text.strip().splitlines()[0]
    if first not in old:
        ENV.write_text(old.rstrip("\n") + ("\n\n" if old else "") + text.strip() + "\n", encoding="utf-8")
