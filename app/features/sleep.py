# sleep from the smart band: a short summary after every sleep (a night shift means the "night" is in the day)
import datetime as dt
import json
import time

from app.log import log
from app.paths import ROOT
from app.services import sleep
from app.telegram import Ctx

SHOW, FORCE = "sleep", "sleep:force"
STATE = ROOT / "sleep_state.json"
CHECK_SEC = 15 * 60  # mi fitness uploads about once an hour, catch it soon after
RETRY_SEC = 15 * 60
MIN_SLEEP = 90  # shorter ones are naps: they count in the totals but get no message
FRESH_SEC = 18 * 3600  # older sleeps that show up late (band was not synced) are not announced
PANEL_DAYS = 14

# called after every sync (the panel keeps its card fresh this way)
on_synced = []
last_error = {"text": None}  # of the last sync, for the health check
_last_try = 0.0


def hm(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h} год {m:02d} хв" if h else f"{m} хв"


def clock(ts: int, tz: int) -> str:
    return dt.datetime.fromtimestamp(ts + tz, dt.UTC).strftime("%H:%M")


def _sleeps(data: dict) -> list:
    # every segment with the score of its day (the app scores only the main sleep of a day)
    out = []
    for day in data["days"]:
        main = max(day["segments"], key=lambda g: g["dur"], default=None)
        out += [{**g, "score": day["score"] if g is main else None} for g in day["segments"]]
    return sorted(out, key=lambda g: g["end"])


def _avg(data: dict, days: int = 7) -> int | None:
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    totals = [d["total"] for d in data["days"] if d["day"] > since]
    return round(sum(totals) / len(totals)) if totals else None


def describe(g: dict, data: dict) -> str:
    lines = [f"😴 Сон {clock(g['start'], g['tz'])} → {clock(g['end'], g['tz'])} · {hm(g['dur'])}"]
    if g.get("score"):
        lines.append(f"Оцінка {g['score']}/100")
    if g["deep"] or g["rem"]:
        lines.append(f"Глибокий {hm(g['deep'])} · Легкий {hm(g['light'])} · REM {hm(g['rem'])}")
    if g["wakes"]:
        lines.append(f"Прокидався {g['wakes']} р. ({hm(g['awake'])})")
    if g.get("hr"):
        lines.append(f"Пульс {g['hr']} ({g['hr_min']}–{g['hr_max']})")
    if avg := _avg(data):
        lines.append(f"За 7 днів у середньому {hm(avg)} на добу")
    return "\n".join(lines)


def summary() -> str:
    data = sleep.cached()
    if not sleep.configured():
        return "😴 Сон ще не підключений: потрібен вхід у Xiaomi (python -m app.services.sleep login)."
    if not data or not data["days"]:
        return "😴 Ще не бачу сну — зараз підтягну з Mi Fitness."
    sleeps = _sleeps(data)
    last = next((g for g in reversed(sleeps) if g["dur"] >= MIN_SLEEP), sleeps[-1])
    upd = dt.datetime.fromtimestamp(data["ts"]).strftime("%d.%m %H:%M")
    woke = time.strftime("%d.%m", time.gmtime(last["end"] + last["tz"]))
    return f"{describe(last, data)}\n\nПрокинувся {woke} · оновлено {upd}"


def stale() -> bool:
    return sleep.age() > CHECK_SEC


def _announced() -> int | None:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))["last"]
    except Exception:
        return None


def _announce(ctx: Ctx, data: dict) -> None:
    sleeps = _sleeps(data)
    if not sleeps:
        return
    last = _announced()
    STATE.write_text(json.dumps({"last": sleeps[-1]["end"]}), encoding="utf-8")
    if last is None:  # first run: history is not news
        return
    new = [g for g in sleeps if g["end"] > last and g["dur"] >= MIN_SLEEP and time.time() - g["end"] < FRESH_SEC]
    if new:
        ctx.send(describe(new[-1], data))


def refresh(ctx: Ctx | None, answer: Ctx | None = None) -> bool:
    # ctx: where to announce a new sleep; answer: someone asked in the chat and waits
    def done(data, err):
        last_error["text"] = str(err) if err else None
        if err:
            log(f"sleep: {err!r}")
        for fn in on_synced:
            try:
                fn()
            except Exception as e:
                log(f"after sleep: {e!r}")
        if data and ctx:
            _announce(ctx, data)
        if answer:
            answer.send(summary() if data else f"😴 Не вийшло підтягнути сон: {err}")

    return sleep.sync_in_background(done)


def handle(ctx: Ctx, text: str) -> bool:
    if text == SHOW:
        ctx.send(summary())
        if stale():
            refresh(ctx)
    elif text == FORCE:
        if not refresh(ctx, ctx):
            ctx.send("⏳ Вже тягну сон, зачекай кілька секунд.")
    else:
        return False
    return True


def tick(ctx: Ctx) -> None:
    global _last_try
    if not sleep.configured() or sleep.is_syncing():
        return
    if stale() and time.time() - _last_try > RETRY_SEC:
        _last_try = time.time()
        refresh(ctx)


def _pieces(g: dict) -> list:
    # a sleep split at local midnight: [date, from minute, to minute]
    start = dt.datetime.fromtimestamp(g["start"] + g["tz"], dt.UTC)
    end = dt.datetime.fromtimestamp(g["end"] + g["tz"], dt.UTC)
    out = []
    while start < end:
        midnight = (start + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0)
        stop = min(end, midnight)
        to = 1440 if stop == midnight else stop.hour * 60 + stop.minute
        out.append([start.date().isoformat(), start.hour * 60 + start.minute, to])
        start = stop
    return out


def panel_data() -> dict | None:
    data = sleep.cached()
    if not data or not data["days"]:
        return None
    sleeps = _sleeps(data)
    since = (dt.date.today() - dt.timedelta(days=PANEL_DAYS - 1)).isoformat()
    days = [
        {k: d[k] for k in ("day", "score", "total", "deep", "light", "rem", "awake", "hr")}
        for d in data["days"]
        if d["day"] >= since
    ]
    timeline = {}
    for g in sleeps:
        for date, a, b in _pieces(g):
            if date >= since:
                timeline.setdefault(date, []).append([a, b, g["dur"] >= MIN_SLEEP])
    last = next((g for g in reversed(sleeps) if g["dur"] >= MIN_SLEEP), sleeps[-1])
    return {
        "upd": dt.datetime.fromtimestamp(data["ts"]).strftime("%d.%m %H:%M"),
        "syncing": sleep.is_syncing(),
        "avg": _avg(data),
        "last": {
            **{
                k: last.get(k)
                for k in ("dur", "deep", "light", "rem", "awake", "wakes", "hr", "hr_min", "hr_max", "score")
            },
            "from": clock(last["start"], last["tz"]),
            "to": clock(last["end"], last["tz"]),
            "date": time.strftime("%d.%m", time.gmtime(last["end"] + last["tz"])),
        },
        "days": days,
        "timeline": timeline,
        "since": since,
    }
