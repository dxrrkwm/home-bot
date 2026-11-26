# "сплю" / "встав": while asleep the bot sends everything silently and holds reminders and pills;
# the automatic mode (laptop idle at night) only makes messages silent
import json
import time

from app.paths import ROOT

STATE = ROOT / "band_state.json"
MAX_SEC = 14 * 3600  # nobody sleeps longer, the mode ends by itself


def _load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(s: dict) -> None:
    STATE.write_text(json.dumps(s), encoding="utf-8")


def since() -> float | None:
    s = _load().get("since")
    return s if s and time.time() - s < MAX_SEC else None


def active() -> bool:
    return since() is not None


def begin(eco_by_us: bool, auto: bool = False, since: float | None = None) -> None:
    s = _load()
    s.update(since=since or time.time(), eco=eco_by_us, auto=auto)
    _save(s)


def hold_eco() -> None:
    # eco stays until the wake up, then the normal mode comes back
    s = _load()
    s["eco"] = True
    _save(s)


def is_auto() -> bool:
    return active() and bool(_load().get("auto"))


def held() -> bool:
    # reminders and pills wait only when the user said "сплю"
    return active() and not _load().get("auto")


def end() -> dict | None:
    # the finished session {"from", "to", "eco"} or None if not asleep
    s = _load()
    if not s.get("since"):
        return None
    session = {
        "from": s["since"],
        "to": min(time.time(), s["since"] + MAX_SEC),
        "eco": s.get("eco", False),
        "auto": s.get("auto", False),
    }
    s.update(since=None, eco=False, auto=False, last=session)
    _save(s)
    return session


def overdue() -> bool:
    # the mode was left on too long (no "встав"): end it quietly
    s = _load().get("since")
    return bool(s) and time.time() - s >= MAX_SEC


def sent(key: str) -> bool:
    return key in _load().get("tips", [])


def mark(key: str) -> None:
    s = _load()
    s["tips"] = (s.get("tips", []) + [key])[-30:]
    _save(s)
