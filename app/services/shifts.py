# work shifts: imported from the monthly infocus schedule (csv) or set day by day in the panel
#   python -m app.services.shifts import FILE.csv    take my rows from the schedule
import csv
import datetime as dt
import io
import json
import sys

from app.paths import ROOT

SHIFTS = ROOT / "shifts.json"  # {"2026-11-27": [{"from": "09:00", "to": "21:00", "extra": false}]}
PRESETS = {"day": ("09:00", "21:00"), "night": ("20:00", "08:00")}  # as in the infocus schedule
NAMES = {"day": "денна", "night": "нічна"}
NIGHT_FROM, NIGHT_TO = 21, 9  # hours in this window are paid as night ones
ME = "@dxrrkwm"  # my row in the schedule, by telegram name


def _norm(value) -> list:
    # old files kept "day" / "night" per date
    if isinstance(value, str):
        a, b = PRESETS[value]
        return [{"from": a, "to": b, "extra": False}]
    return value


def load() -> dict:
    try:
        return {d: _norm(v) for d, v in json.loads(SHIFTS.read_text(encoding="utf-8")).items()}
    except Exception:
        return {}


def _save(items: dict) -> None:
    old = (dt.date.today() - dt.timedelta(days=90)).isoformat()
    items = {d: v for d, v in sorted(items.items()) if d >= old and v}
    SHIFTS.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def _span(day: str, s: dict) -> tuple:
    start = dt.datetime.combine(dt.date.fromisoformat(day), dt.time.fromisoformat(s["from"]))
    end = dt.datetime.combine(start.date(), dt.time.fromisoformat(s["to"]))
    if end <= start:  # a night shift ends the next morning
        end += dt.timedelta(days=1)
    return start, end


def kind(start: dt.datetime, end: dt.datetime) -> str:
    return "night" if end.date() > start.date() else "day"


def preset(day: str) -> str | None:
    # the preset a date matches in the panel calendar, None for off days or own hours
    main = [s for s in load().get(day, []) if not s["extra"]]
    if not main:
        return None
    return kind(*_span(day, main[0]))


def hour(h: int) -> str:
    return f"{int(h) % 24:02d}:00"


def set_main(day: str, start: int | None = None, end: int | None = None) -> None:
    # the panel calendar: own hours for the main shift of a date, or an off day; extra hours stay
    items = load()
    keep = [s for s in items.get(day, []) if s["extra"]]
    if start is not None:
        keep.insert(0, {"from": hour(start), "to": hour(end), "extra": False, "manual": True})
    items[day] = keep
    _save(items)


def remove_extra(day: str, start: int, end: int) -> None:
    items = load()
    items[day] = [
        s for s in items.get(day, []) if not (s["extra"] and (s["from"], s["to"]) == (hour(start), hour(end)))
    ]
    _save(items)


def common(limit: int = 6) -> list:
    # the usual main shifts ("10-22", "20-8"), most frequent first: quick picks in the panel
    count = {}
    for items in load().values():
        for s in items:
            if not s["extra"]:
                key = (int(s["from"][:2]), int(s["to"][:2]))
                count[key] = count.get(key, 0) + 1
    picks = sorted(count, key=lambda k: -count[k])[:limit]
    return sorted(picks, key=lambda k: (k[1] < k[0], k[0]))  # day shifts by the start, nights last


def all_shifts(extra: bool = True) -> list:
    # [(start, end, kind, extra)] sorted
    out = []
    for day, items in load().items():
        for s in items:
            if extra or not s["extra"]:
                start, end = _span(day, s)
                out.append((start, end, kind(start, end), s["extra"]))
    return sorted(out)


LONG_EXTRA = dt.timedelta(hours=6)  # extra hours this long count as a shift for sleep


def _work() -> list:
    return [s for s in all_shifts() if not s[3] or s[1] - s[0] >= LONG_EXTRA]


def current(now: dt.datetime | None = None) -> tuple | None:
    now = now or dt.datetime.now()
    return next((s for s in _work() if s[0] <= now < s[1]), None)


def upcoming(now: dt.datetime | None = None) -> tuple | None:
    now = now or dt.datetime.now()
    return next((s for s in _work() if s[0] > now), None)


def previous(now: dt.datetime | None = None) -> tuple | None:
    now = now or dt.datetime.now()
    return next((s for s in reversed(_work()) if s[1] <= now), None)


def label(shift: tuple, now: dt.datetime | None = None) -> str:
    start, end = shift[0], shift[1]
    today = (now or dt.datetime.now()).date()
    when = {0: "сьогодні", 1: "завтра"}.get((start.date() - today).days, f"{start:%d.%m}")
    name = "допка" if shift[3] else NAMES[shift[2]]
    return f"{when} {name} {start:%H:%M}–{end:%H:%M}"


def _night_minutes(start: dt.datetime, end: dt.datetime) -> int:
    out, t = 0, start
    while t < end:
        if t.hour >= NIGHT_FROM or t.hour < NIGHT_TO:
            out += 1
        t += dt.timedelta(minutes=1)
    return out


def hours(year: int, month: int) -> dict:
    # worked hours of a month by the date the shift starts, split like the schedule does
    out = {"day": 0.0, "night": 0.0, "extra_day": 0.0, "extra_night": 0.0}
    for start, end, _, extra in all_shifts():
        if (start.year, start.month) != (year, month):
            continue
        night = _night_minutes(start, end) / 60
        total = (end - start).total_seconds() / 3600
        prefix = "extra_" if extra else ""
        out[prefix + "day"] += total - night
        out[prefix + "night"] += night
    return out


def salary(year: int, month: int, pay: dict) -> dict | None:
    # an estimate from the hours and the rates in config.json "pay"; extra hours cost more past a limit
    h = hours(year, month)
    if not any(h.values()) or not pay:
        return None

    def extra(hours_, rate, over_rate, limit):
        return min(hours_, limit) * rate + max(0, hours_ - limit) * over_rate

    base = h["day"] * pay["day"] + h["night"] * pay["night"]
    add = extra(h["extra_day"], pay["extra_day"], pay["extra_day_over"], pay["extra_day_limit"])
    add += extra(h["extra_night"], pay["extra_night"], pay["extra_night_over"], pay["extra_night_limit"])
    return {
        "hours": h,
        "base": round(base),
        "extra": round(add),
        "kpi": pay.get("kpi", 0),
        "total": round(base + add + pay.get("kpi", 0)),
    }


def parse_schedule(text: str, who: str = ME) -> tuple[int, int, dict]:
    # the infocus sheet: row 1 day names, row 2 day numbers (two columns a day), my row "осн" and the next one "доп"
    rows = list(csv.reader(io.StringIO(text)))
    days = {i: int(v) for i, v in enumerate(rows[1]) if v.strip().isdigit()}
    me = next(i for i, r in enumerate(rows) if len(r) > 3 and r[2].strip().strip('"') == who and r[3] == "осн")
    found = {}
    for row, extra in ((rows[me], False), (rows[me + 1], True)):
        for col, n in days.items():
            a, b = row[col].strip(), row[col + 1].strip()
            if a.isdigit() and b.isdigit():
                found.setdefault(n, []).append(
                    {"from": f"{int(a) % 24:02d}:00", "to": f"{int(b) % 24:02d}:00", "extra": extra}
                )
    return found


def _overlap(day: str, a: dict, b: dict) -> bool:
    (a1, a2), (b1, b2) = _span(day, a), _span(day, b)
    return a1 < b2 and b1 < a2


def add(day: str, start: str, end: str, extra: bool = True) -> None:
    # a shift taken after the schedule came out ("manual"): kept until the schedule shows it
    items = load()
    items.setdefault(day, []).append({"from": start, "to": end, "extra": extra, "manual": True})
    _save(items)


def import_month(text: str, year: int, month: int) -> dict:
    # replaces the month with the schedule, keeps manual shifts the schedule does not have yet
    found = parse_schedule(text)
    prefix = f"{year:04d}-{month:02d}-"
    old = load()
    items = {d: v for d, v in old.items() if not d.startswith(prefix)}
    month_items = {f"{prefix}{n:02d}": v for n, v in found.items()}
    for day, day_items in old.items():
        if not day.startswith(prefix):
            continue
        for s in day_items:
            if s.get("manual") and not any(_overlap(day, s, t) for t in month_items.get(day, [])):
                month_items.setdefault(day, []).append(s)
    _save(items | month_items)
    return month_items


MONTHS = {
    "январ": 1, "січ": 1, "феврал": 2, "лют": 2, "март": 3, "берез": 3, "апрел": 4, "квіт": 4,
    "май": 5, "мая": 5, "трав": 5, "июн": 6, "черв": 6, "июл": 7, "лип": 7, "август": 8, "серп": 8,
    "сентябр": 9, "верес": 9, "октябр": 10, "жовт": 10, "ноябр": 11, "листоп": 11, "декабр": 12, "груд": 12,
}  # fmt: skip


def month_of(name: str, today: dt.date | None = None) -> tuple[int, int] | None:
    # "2026 график infocus - Декабрь.csv" -> (2026, 12); the year from the name or the nearest one
    low = name.lower()
    month = next((m for key, m in sorted(MONTHS.items(), key=lambda kv: -len(kv[0])) if key in low), None)
    if not month:
        return None
    today = today or dt.date.today()
    digits = "".join(c for c in name if c in "0123456789")
    year = int(digits[:4]) if len(digits) >= 4 and digits[:2] == "20" else today.year
    if month < today.month - 6:
        year = max(year, today.year + 1)
    return year, month


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "import":
        path = sys.argv[2]
        ym = month_of(path)
        text = open(path, encoding="utf-8-sig").read()
        got = import_month(text, *ym)
        print(ym, len(got), "days", hours(*ym))
