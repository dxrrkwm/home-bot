# sleep mode ("сплю" in the chat or the panel, or by itself when the laptop is idle at night),
# bedtime tips for the shifts, a short morning brief, steps and pulse from the band
import datetime as dt
import re
import threading
import time

from app import config, devices
from app.features import light, money, reminders
from app.features import sleep as band_sleep
from app.log import log
from app.services import outage, pills, planner, shifts, sleep, sleepmode
from app.telegram import Ctx, download

SLEEP, WAKE, PANEL = "band:sleep", "band:wake", "band-panel"
# from the panel calendar: shiftset:2026-12-21:10-22 or :off, extraadd:2026-12-21:22-23, extradel:...
SHIFT_SET, EXTRA_ADD, EXTRA_DEL = "shiftset:", "extraadd:", "extradel:"
# "допка 19.12 9-21" (extra hours) or "зміна 19.12 9-21" (a main shift), taken after the schedule came out
ADD_RE = re.compile(
    r"^(допк\w*|доп|зміна|смена)\s+(\d{1,2})\.(\d{1,2})\s+(\d{1,2})(?::00)?\s*[-–]\s*(\d{1,2})(?::00)?$", re.I
)
NEED_MIN = 8 * 60
FALL_ASLEEP_MIN = 15
GET_READY_MIN = 45  # from waking up to the start of a day shift (works from home)
DAY_TIP_BEFORE_MIN = 60  # the bedtime tip comes this long before the bedtime
NIGHT_TIP_BEFORE_H = 6
NAP = ("16:00", "19:00")
SUNRISE_STEPS = [(1, 0), (10, 40), (30, 40), (60, 40), (80, 40)]  # (brightness, seconds before it)
WARM_K = 2700
AUTO_WAKE_AFTER_SEC = 30 * 60
AUTO_IDLE_SEC = 40 * 60  # the laptop untouched this long at night: probably asleep, messages go silent
AUTO_BACK_SEC = 2 * 60  # input again: awake
AUTO_NIGHT = (23, 7)  # hours when idle means sleep, plus a few hours after a night shift
AUTO_AFTER_NIGHT = dt.timedelta(hours=4)
AUTO_BRIEF_SEC = 3 * 3600  # an automatic sleep this long gets the morning brief when it ends
STEPS_GOAL = 8000  # config.json "steps_goal" overrides
FRESH_SEC = (
    10 * 60
)  # the panel asks the cloud again when the data is older  # the band shows a finished sleep this long ago: the mode ends by itself

# called when the state changes (the panel keeps its card fresh this way)
on_changed = []


def _changed() -> None:
    for fn in on_changed:
        try:
            fn()
        except Exception as e:
            log(f"after band: {e!r}")


def debt() -> tuple[int, int] | None:
    # sleep missing to 8 h a day over the last 7 days with data: (minutes, days)
    data = sleep.cached()
    since = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    days = [d["total"] for d in (data or {}).get("days", []) if d["day"] > since]
    if not days:
        return None
    return sum(max(0, NEED_MIN - t) for t in days), len(days)


def _bedtime(start: dt.datetime) -> dt.datetime:
    extra = 30 if (debt() or (0, 0))[0] > 4 * 60 else 0
    return start - dt.timedelta(minutes=GET_READY_MIN + NEED_MIN + FALL_ASLEEP_MIN + extra)


def advice(now: dt.datetime | None = None) -> str | None:
    # what to do about sleep before the next shift, one line
    now = now or dt.datetime.now()
    cur, nxt, prev = shifts.current(now), shifts.upcoming(now), shifts.previous(now)
    if cur:
        return f"Зараз {shifts.NAMES[cur[2]]} зміна до {cur[1]:%H:%M}."
    if prev and prev[2] == "night" and now - prev[1] < dt.timedelta(hours=3):
        if nxt and nxt[0] - now < dt.timedelta(hours=4):
            # back to back: no real sleep fits in, at least a short nap
            return f"Після нічної одразу {shifts.label(nxt, now)} — подрімай хоч 20–40 хв, відсипайся після {nxt[1]:%H:%M}."
        wake = min(prev[1] + dt.timedelta(hours=8, minutes=30), nxt[0] - dt.timedelta(hours=2)) if nxt else None
        return "Після нічної — спати одразу" + (f", до ~{wake:%H:%M}." if wake else ".")
    if not nxt or nxt[0] - now > dt.timedelta(hours=36):
        return None
    when = shifts.label(nxt, now)
    when = when[0].upper() + when[1:]
    if nxt[2] == "day":
        bed = _bedtime(nxt[0])
        if bed > now:
            return f"{when}: щоб поспати 8 год, лягай до {bed:%H:%M}."
        left = int((nxt[0] - now).total_seconds() // 60) - GET_READY_MIN - FALL_ASLEEP_MIN
        return f"{when}: лягай зараз — вийде {band_sleep.hm(max(0, left))}."
    if prev and prev[2] == "night" and nxt[0] - prev[1] < dt.timedelta(hours=13):
        return f"{when}: між нічними спи вранці після зміни."
    return f"{when}: перед нею подрімай {NAP[0]}–{NAP[1]}."


def _tips(ctx: Ctx) -> None:
    now = dt.datetime.now()
    nxt = shifts.upcoming(now)
    if not nxt or sleepmode.active():
        return
    key = f"{nxt[0]:%Y-%m-%d %H}"
    if nxt[2] == "day":
        due = _bedtime(nxt[0]) - dt.timedelta(minutes=DAY_TIP_BEFORE_MIN)
    else:
        due = nxt[0] - dt.timedelta(hours=NIGHT_TIP_BEFORE_H)
    if due <= now < due + dt.timedelta(hours=2) and not sleepmode.sent(key):
        sleepmode.mark(key)
        tip = advice(now)
        if tip:
            ctx.send(f"🌙 {tip}\nЛягаєш — напиши «сплю» (можна з браслета).")


def _lamp_off() -> None:
    try:
        light.with_lamp(lambda lamp: lamp.power(False))
    except Exception as e:
        log(f"сон, лампа: {e!r}")


def _sunrise() -> None:
    # warm light from 1% up, a couple of minutes, so waking up is not a flash
    try:
        light.with_lamp(lambda lamp: (lamp.power(True), lamp.ct(WARM_K)))
        for bright, wait in SUNRISE_STEPS:
            time.sleep(wait)
            light.with_lamp(lambda lamp, b=bright: lamp.bright(b))
    except Exception as e:
        log(f"ранок, лампа: {e!r}")


def _mode(mode: str) -> None:
    devices.switch(mode)
    _changed()


def go_to_sleep(ctx: Ctx) -> None:
    if sleepmode.held():  # an automatic quiet mode becomes the full one
        ctx.send("🌙 Вже сплю. Прокинешся — «встав».")
        return
    ac = devices.power()[0]
    eco = ac is not None and not devices.is_eco()
    sleepmode.begin(eco_by_us=eco)
    ctx.send("🌙 Добраніч. Лампу вимикаю" + (", ноут в Еко" if eco else "") + ". До «встав» усе тихо.")
    # slow parts in the background: the chat and the panel stay responsive
    threading.Thread(target=lambda: (_lamp_off(), eco and _mode("eco"), _changed()), daemon=True).start()


def _short(text: str, size: int = 40) -> str:
    return text if len(text) <= size else text[: size - 1].rstrip() + "…"


def _brief(session: dict) -> str:
    now = dt.datetime.now()
    a, b = dt.datetime.fromtimestamp(session["from"]), dt.datetime.fromtimestamp(session["to"])
    mins = int((b - a).total_seconds() // 60)
    lines = [f"☀️ Доброго ранку! Спав {band_sleep.hm(mins)} ({a:%H:%M}–{b:%H:%M})."]
    if d := debt():
        lines.append(f"Недосип за {d[1]} дн.: {band_sleep.hm(d[0])}." if d[0] else "За тиждень висипаєшся 👍")
    cur, nxt = shifts.current(now), shifts.upcoming(now)
    if cur:
        lines.append(f"Зараз {shifts.NAMES[cur[2]]} зміна до {cur[1]:%H:%M}.")
    elif nxt and nxt[0].date() <= now.date() + dt.timedelta(days=1):
        lines.append(f"Зміна: {shifts.label(nxt, now)}.")
    elif shifts.load():
        lines.append("Сьогодні вихідний.")
    today = now.date()
    # doses of today that were held during the sleep or are still ahead
    after = f"{a:%H:%M}" if a.date() == today else "00:00"
    doses = [f"{m['name']} {t}" for m in pills.meds() if pills.is_day(m, today) for t in m["times"] if t >= after]
    if doses:
        lines.append("💊 " + ", ".join(doses))
    rems = [r for r in reminders.load() if dt.datetime.fromtimestamp(r["ts"]).date() == today]
    if rems:
        lines.append(
            "⏰ " + "; ".join(f"{dt.datetime.fromtimestamp(r['ts']):%H:%M} {_short(r['text'])}" for r in rems[:3])
        )
    group = config.load().get("outage_group")
    offs = [(s, e) for s, e, _ in outage.intervals(group) if e > now and s.date() == today] if group else []
    if offs:
        lines.append("💡 Без світла: " + ", ".join(f"{s:%H:%M}–{e:%H:%M}" for s, e in offs))
    return "\n".join(lines)


def wake_up(ctx: Ctx, lamp: bool = True) -> None:
    session = sleepmode.end()
    if not session:
        ctx.send("☀️ Ти й так не в режимі сну.")
        return
    ctx.send(_brief(session))
    ac = devices.power()[0]
    back = session["eco"] and ac

    def run():
        if lamp:
            _sunrise()
        if back:
            _mode("normal")
        _changed()

    threading.Thread(target=run, daemon=True).start()


def import_schedule(name: str, data: bytes) -> str:
    # the monthly infocus sheet: my shifts go to shifts.json, the monthly reminder stops
    ym = shifts.month_of(name)
    if not ym:
        return "📅 Не зрозумів, за який це місяць — у назві файлу має бути місяць (як у таблиці infocus)."
    try:
        got = shifts.import_month(data.decode("utf-8-sig"), *ym)
    except StopIteration:
        return f"📅 Не знайшов у файлі свій рядок ({shifts.ME})."
    year, month = ym
    reminders.close("schedule:")
    prev = dt.date(year, month, 1) - dt.timedelta(days=1)
    planner.mark(f"schedule:{prev:%Y-%m}")
    main = [s for items in got.values() for s in items if not s["extra"]]
    nights = sum(1 for s in main if s["to"] <= s["from"])
    extra = sum(1 for items in got.values() for s in items if s["extra"])
    lines = [
        f"📅 Графік на {money.MONTHS[month - 1]}: {len(main)} змін ({len(main) - nights} денних, {nights} нічних)"
        + (f", ще {extra} додаткових." if extra else ".")
    ]
    if pay := next((p for p in money.salary(config.load()) if p["month"] == money.MONTHS[month - 1]), None):
        lines.append(f"{pay['hours']} год → ≈ {money.uah(pay['total'])} грн з KPI, прийде ~{pay['paid']}.")
    lines.append("Поради, коли лягати, — перед кожною зміною, все у вкладці «Браслет».")
    _changed()
    return "\n".join(lines)


def document(ctx: Ctx, doc: dict) -> None:
    name = doc.get("file_name", "")
    if not name.lower().endswith(".csv"):
        ctx.send("📎 Файли розумію лише одного виду: графік змін у CSV (таблиця infocus → Файл → Завантажити → CSV).")
        return
    try:
        ctx.send(import_schedule(name, download(ctx.token, doc["file_id"])))
    except Exception as e:
        log(f"графік: {e!r}")
        ctx.send(f"📅 Не вийшло прочитати графік: {e}")


def add_shift(ctx: Ctx, m: re.Match) -> None:
    today = dt.date.today()
    day = dt.date(today.year, int(m[3]), int(m[2]))
    if day < today - dt.timedelta(days=60):
        day = day.replace(year=today.year + 1)
    extra = m[1].lower().startswith("доп")
    shifts.add(day.isoformat(), f"{int(m[4]) % 24:02d}:00", f"{int(m[5]) % 24:02d}:00", extra)
    what = "Допку" if extra else "Зміну"
    lines = [f"👌 {what} {day:%d.%m} {int(m[4]):02d}:00–{int(m[5]) % 24:02d}:00 внесено."]
    if pay := next((p for p in money.salary(config.load()) if p["month"] == money.MONTHS[day.month - 1]), None):
        lines.append(
            f"{money.MONTHS[day.month - 1].capitalize()}: {pay['hours']} год → ≈ {money.uah(pay['total'])} грн з KPI."
        )
    _changed()
    ctx.send("\n".join(lines))


def handle(ctx: Ctx, text: str) -> bool:
    if m := ADD_RE.match(text.strip()):
        add_shift(ctx, m)
        return True
    if text == SLEEP:
        go_to_sleep(ctx)
    elif text == WAKE:
        wake_up(ctx)
    elif text.startswith((SHIFT_SET, EXTRA_ADD, EXTRA_DEL)):
        cmd, day, hours = text.split(":")
        start, end = map(int, hours.split("-")) if hours != "off" else (None, None)
        if cmd + ":" == SHIFT_SET:
            shifts.set_main(day, start, end)
        elif cmd + ":" == EXTRA_ADD:
            shifts.add(day, shifts.hour(start), shifts.hour(end))
        else:
            shifts.remove_extra(day, start, end)
        _changed()
    else:
        return False
    return True


def _sleepy_hours(now: dt.datetime) -> bool:
    if shifts.current(now):
        return False
    prev = shifts.previous(now)
    after_night = prev and prev[2] == "night" and now - prev[1] < AUTO_AFTER_NIGHT
    return now.hour >= AUTO_NIGHT[0] or now.hour < AUTO_NIGHT[1] or bool(after_night)


def _finish() -> None:
    # the sleep ends without the brief; the normal mode comes back if the sleep held eco
    session = sleepmode.end()
    if session and session["eco"] and devices.power()[0]:
        devices.switch("normal")
    _changed()


def _auto(ctx: Ctx) -> None:
    # the laptop tells when nobody uses it: silent messages at night, sound back on the first touch
    idle = devices.idle_sec()
    if idle is None:
        return
    since = sleepmode.since()
    if since and sleepmode.is_auto() and idle < AUTO_BACK_SEC:
        if time.time() - since >= AUTO_BRIEF_SEC:
            wake_up(ctx, lamp=False)
        else:
            _finish()
    elif not since and idle >= AUTO_IDLE_SEC and _sleepy_hours(dt.datetime.now()):
        sleepmode.begin(eco_by_us=False, auto=True, since=time.time() - idle)
        log("сон: ноут без руху вночі, повідомлення без звуку")
        _changed()


def tick(ctx: Ctx) -> None:
    _auto(ctx)
    if sleepmode.overdue():
        _finish()
    since = sleepmode.since()
    if since:
        # the band already shows this sleep as finished: "встав" was forgotten
        data = sleep.cached() or {"days": []}
        ends = [g["end"] for d in data["days"] for g in d["segments"] if g["end"] > since]
        if ends and time.time() - max(ends) > AUTO_WAKE_AFTER_SEC:
            wake_up(ctx, lamp=False)
        return
    _tips(ctx)


def _pieces(start: dt.datetime, end: dt.datetime) -> list:
    out = []
    while start < end:
        midnight = dt.datetime.combine(start.date() + dt.timedelta(days=1), dt.time())
        stop = min(end, midnight)
        out.append(
            [
                start.date().isoformat(),
                start.hour * 60 + start.minute,
                1440 if stop == midnight else stop.hour * 60 + stop.minute,
            ]
        )
        start = stop
    return out


def _streak(days: list, goal: int) -> int:
    # days in a row at the goal, today counts only once reached
    n = 0
    for i, (day, steps) in enumerate(reversed(days)):
        if steps >= goal:
            n += 1
        elif i:  # today is not over yet
            break
    return n


def activity(now: dt.datetime | None = None) -> dict | None:
    data = sleep.cached() or {}
    act = data.get("activity") or {}
    if not act:
        return None
    now = now or dt.datetime.now()
    goal = config.load().get("steps_goal", STEPS_GOAL)
    today = act.get(now.date().isoformat(), {})
    days = [((now.date() - dt.timedelta(days=i)).isoformat()) for i in range(13, -1, -1)]
    steps = [(d, act.get(d, {}).get("steps", 0)) for d in days]
    worn = [s for _, s in steps[-8:-1] if s]  # the last full days with the band on
    best = max(steps, key=lambda x: x[1])
    pulse = data.get("pulse")
    # the inner ring: the last main sleep if it ended in the last 18 h
    last = (band_sleep.panel_data() or {}).get("last") or {}
    sleeps = [g for d in data.get("days", []) for g in d["segments"] if g["dur"] >= band_sleep.MIN_SLEEP]
    recent = max(sleeps, key=lambda g: g["end"], default=None)
    slept = recent["dur"] if recent and time.time() - recent["end"] < 18 * 3600 else None
    return {
        "steps": today.get("steps", 0),
        "km": today.get("km", 0),
        "kcal": today.get("kcal", 0),
        "rhr": today.get("rhr"),
        "goal": goal,
        "days": steps,
        "avg7": round(sum(worn) / len(worn)) if worn else None,
        "best": {"day": best[0], "steps": best[1]} if best[1] else None,
        "streak": _streak(steps, goal),
        "km7": round(sum(act.get(d, {}).get("km", 0) for d in days[-7:]), 1),
        "pulse": {"bpm": pulse["bpm"], "ago": int((time.time() - pulse["time"]) // 60)} if pulse else None,
        "slept": slept,
        "score": last.get("score") if slept else None,
    }


def _hours(s: dict) -> str:
    return f"{int(s['from'][:2])}–{int(s['to'][:2])}"


def panel_data() -> dict:
    now = dt.datetime.now()
    since = sleepmode.since()
    sleep_data = band_sleep.panel_data()
    work = {}
    for start, end, _, extra in shifts.all_shifts():
        for day, a, b in _pieces(start, end):
            work.setdefault(day, []).append([a, b, extra])
    # calendar cells: hours of the main shift ("8–20"), night or not, hours of the extra ones
    calendar = {}
    for day, items in shifts.load().items():
        main = next((s for s in items if not s["extra"]), None)
        extra = [s for s in items if s["extra"]]
        calendar[day] = {
            "main": _hours(main) if main else None,
            "night": shifts.preset(day) == "night",
            "extras": [_hours(s) for s in extra],
            "extra_h": round(sum((b - a).total_seconds() for a, b in (shifts._span(day, s) for s in extra)) / 3600),
        }
    d = debt()
    nxt = shifts.upcoming(now)
    return {
        "sleeping": dt.datetime.fromtimestamp(since).strftime("%H:%M") if since else None,
        "auto": sleepmode.is_auto(),
        "slept": int((time.time() - since) // 60) if since else None,
        "next": shifts.label(nxt, now) if nxt else None,
        "advice": advice(now),
        "debt": {"min": d[0], "days": d[1]} if d else None,
        "shifts": calendar,
        "common": [f"{a}–{b}" for a, b in shifts.common()],
        "work": work,
        "sleep": sleep_data,
        "activity": activity(now),
        "today": now.date().isoformat(),
    }
