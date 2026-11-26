# work shifts, 12 h on a 2/2 schedule: day 09–21 or night 21–09, set day by day in the panel
import datetime as dt
import json

from app.paths import ROOT

SHIFTS = ROOT / "shifts.json"  # {"2026-11-27": "day", "2026-11-28": "night"}
START = {"day": 9, "night": 21}
NAMES = {"day": "денна", "night": "нічна"}
CYCLE = {None: "day", "day": "night", "night": None}
HOURS = 12


def load() -> dict:
    try:
        return json.loads(SHIFTS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def cycle(day: str) -> str | None:
    # a tap in the calendar: none -> day -> night -> none
    items = load()
    kind = CYCLE[items.get(day)]
    if kind:
        items[day] = kind
    else:
        items.pop(day, None)
    old = (dt.date.today() - dt.timedelta(days=60)).isoformat()
    items = {d: k for d, k in sorted(items.items()) if d >= old}
    SHIFTS.write_text(json.dumps(items, indent=1), encoding="utf-8")
    return kind


def all_shifts() -> list:
    # [(start, end, kind)] sorted
    out = []
    for day, kind in load().items():
        start = dt.datetime.combine(dt.date.fromisoformat(day), dt.time(START[kind]))
        out.append((start, start + dt.timedelta(hours=HOURS), kind))
    return sorted(out)


def current(now: dt.datetime | None = None) -> tuple | None:
    now = now or dt.datetime.now()
    return next((s for s in all_shifts() if s[0] <= now < s[1]), None)


def upcoming(now: dt.datetime | None = None) -> tuple | None:
    now = now or dt.datetime.now()
    return next((s for s in all_shifts() if s[0] > now), None)


def previous(now: dt.datetime | None = None) -> tuple | None:
    now = now or dt.datetime.now()
    return next((s for s in reversed(all_shifts()) if s[1] <= now), None)


def label(shift: tuple, now: dt.datetime | None = None) -> str:
    start, end, kind = shift
    today = (now or dt.datetime.now()).date()
    days = (start.date() - today).days
    when = {0: "сьогодні", 1: "завтра"}.get(days, f"{start:%d.%m}")
    return f"{when} {NAMES[kind]} {start:%H:%M}–{end:%H:%M}"
