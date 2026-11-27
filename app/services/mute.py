# "тиша": reminders and pills wait for a while and come after it, like during the sleep
import datetime as dt
import json
import re
import time

from app.paths import ROOT

STATE = ROOT / "mute_state.json"
MORNING = 9  # "до ранку"

# "тиша 1 год", "тиша 30 хв", "тиша до 14:30", "тиша до ранку", "тиша вимк"
FOR_RE = re.compile(r"^(\d+)\s*(хв|мин|м|год|час|ч|г|h)\w*\.?$", re.I)
TILL_RE = re.compile(r"^до\s+(\d{1,2})(?:[:.](\d{2}))?$", re.I)
OFF_WORDS = {"вимк", "вимкнути", "off", "стоп", "зняти", "викл"}


def until() -> float | None:
    try:
        ts = json.loads(STATE.read_text(encoding="utf-8")).get("until")
    except Exception:
        return None
    return ts if ts and ts > time.time() else None


def active() -> bool:
    return until() is not None


def set_until(ts: float | None) -> None:
    STATE.write_text(json.dumps({"until": ts}), encoding="utf-8")


def _at(h: int, m: int = 0) -> float:
    now = dt.datetime.now()
    t = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if t <= now:
        t += dt.timedelta(days=1)
    return t.timestamp()


def parse(arg: str) -> float | None:
    # the end of the silence for "1 год" / "до 14:30" / "до ранку", 0 to switch it off, None if not understood
    arg = arg.strip().lower()
    if arg in OFF_WORDS:
        return 0
    if arg in ("до ранку", "до утра"):
        return _at(MORNING)
    if m := FOR_RE.match(arg):
        n = int(m.group(1))
        return time.time() + n * (3600 if m.group(2)[0] in "гчh" else 60)
    if m := TILL_RE.match(arg):
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        return _at(h, mi) if h < 24 and mi < 60 else None
    if arg.isdigit():  # from the panel: minutes
        return time.time() + int(arg) * 60
    return None
