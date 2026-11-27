import datetime as dt
import json
import re
import threading
import time

from app import keyboards as kb
from app.paths import REMINDERS
from app.services import mute, planner, sleepmode
from app.telegram import Ctx

REMIND_RE = re.compile(r"^(?:нагадай|напомни|remind)\s+(.*)$", re.I | re.S)
IN_RE = re.compile(r"^через\s+(\d+)\s*(хв|мин|м|min|год|час|ч|г|h)\w*\.?\s*(.*)$", re.I | re.S)
AT_RE = re.compile(r"^(?:о\s+|в\s+|на\s+)?(завтра\s+)?(\d{1,2})[:.](\d{2})\s*(.*)$", re.I | re.S)
# at the end of the text: "кожні 5 хв", "кожні 2 год", "без повтору"
REPEAT_RE = re.compile(r"\s*(?:кожні\s+(\d+)\s*(хв|мин|м|год|час|ч|г)\w*\.?|без\s+повтор\w*)\s*$", re.I)

REPEAT_MIN = 10  # until "done" is pressed, like pills
GIVE_UP_HOURS = 8
DAY_HOURS = range(10, 23)  # "day_only" reminders wait for these hours
SNOOZE_MIN = 30  # old buttons without minutes
SNOOZE = (15, 60, 180)
# silence for all reminders and pills: label -> argument of "тиша"
MUTE_PRESETS = (("1 год", "1 год"), ("3 год", "3 год"), ("до ранку", "до ранку"))
MUTE_RE = re.compile(r"^(?:тиша|тишина|мут|mute)(?:\s+(.*))?$", re.I | re.S)

HELP = (
    "➕ Щоб додати нагадування, просто напиши:\n"
    "нагадай 20:00 подзвонити\nнагадай через 30 хв зняти чайник\nнагадай завтра 9:00 зустріч\n"
    f"Повторюю кожні {REPEAT_MIN} хв, поки не натиснеш «✅ Зроблено». Інакше: …кожні 5 хв або …без повтору\n\n"
    "💊 Таблетку:\nтаблетка Назва 09:00\nтаблетка Назва 09:00, 21:00\nтаблетка Назва 10:00 кожні 2 дні"
)

_lock = threading.Lock()  # tick and commands from the panel both write the file


def load() -> list:
    try:
        items = json.loads(REMINDERS.read_text(encoding="utf-8"))
    except Exception:
        return []
    for r in items:  # older items had no id and no repeat
        r.setdefault("id", f"{r['ts']:.0f}")
        r.setdefault("every", 0)
    return items


def save(items: list) -> None:
    REMINDERS.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def _repeat(what: str) -> tuple[str, int]:
    m = REPEAT_RE.search(what)
    if not m:
        return what, REPEAT_MIN
    every = 0 if not m.group(1) else int(m.group(1)) * (60 if m.group(2).lower()[0] in "гчh" else 1)
    return what[: m.start()], every


def parse(text: str) -> dict | None:
    # "нагадай 20:00 текст" / "нагадай через 30 хв текст" / "нагадай завтра 9:00 текст" [кожні 5 хв | без повтору]
    m = REMIND_RE.match(text.strip())
    if not m:
        return None
    rest = m.group(1).strip()
    now = time.time()
    if m2 := IN_RE.match(rest):
        n, unit, what = int(m2.group(1)), m2.group(2).lower(), m2.group(3)
        ts = now + n * (3600 if unit[0] in "гчh" else 60)
    elif m2 := AT_RE.match(rest):
        tomorrow, h, mi, what = m2.group(1), int(m2.group(2)), int(m2.group(3)), m2.group(4)
        if h > 23 or mi > 59:
            return None
        t = time.localtime(now)
        ts = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, h, mi, 0, 0, 0, -1))
        if tomorrow or ts <= now:
            ts += 86400
    else:
        return None
    what, every = _repeat(what)
    return {"id": f"{ts:.0f}", "ts": ts, "at": ts, "text": what.strip() or "⏰", "every": every}


def _when(ts: float) -> str:
    return time.strftime("%d.%m %H:%M", time.localtime(ts))


def every_text(every: int) -> str:
    if not every:
        return "без повтору"
    return f"кожні {every // 60} год" if every % 60 == 0 else f"кожні {every} хв"


def text_list() -> str:
    items = sorted(load(), key=lambda r: r["ts"])
    muted = mute_text() + "\n\n" if mute.active() else ""
    if not items:
        return muted + "⏰ Нагадувань немає.\n\n" + HELP
    return (
        muted
        + "⏰ Нагадування:\n"
        + "\n".join(f"{_when(r['ts'])} — {r['text']} ({every_text(r['every'])})" for r in items)
        + "\n\nВидалити — кнопкою нижче."
    )


def buttons() -> list | None:
    items = sorted(load(), key=lambda r: r["ts"])
    rows = [[{"text": f"❌ {_when(r['ts'])} {r['text']}"[:48], "callback_data": f"delrem:{r['id']}"}] for r in items]
    if len(items) > 1:
        rows.append([{"text": "🗑 Видалити всі", "callback_data": "clear_rem"}])
    return rows + mute_buttons()


def _ping_buttons(r: dict) -> list:
    return [
        [{"text": "✅ Зроблено", "callback_data": f"remdone:{r['id']}"}],
        [{"text": f"💤 {_mins(m)}", "callback_data": f"remsnooze:{r['id']}:{m}"} for m in SNOOZE],
    ]


def _mins(m: int) -> str:
    return f"{m // 60} год" if m % 60 == 0 else f"{m} хв"


def mute_text() -> str:
    ts = mute.until()
    if not ts:
        return "🔔 Нагадування й таблетки увімкнені."
    return f"🔕 Тиша до {time.strftime('%H:%M', time.localtime(ts))} — усе, що випаде, прийде після."


def mute_buttons() -> list:
    rows = [[{"text": f"🔕 {label}", "callback_data": f"тиша {arg}"} for label, arg in MUTE_PRESETS]]
    if mute.active():
        rows.append([{"text": "🔔 Зняти тишу", "callback_data": "тиша вимк"}])
    return rows


def _mute(ctx: Ctx, arg: str) -> None:
    if not arg:
        ctx.send(mute_text() + "\n\nСвій час: тиша 2 год, тиша 45 хв, тиша до 14:30", inline=mute_buttons())
        return
    ts = mute.parse(arg)
    if ts is None:
        ctx.send("🔕 Не зрозумів. Приклади: тиша 1 год, тиша 30 хв, тиша до 14:30, тиша до ранку, тиша вимк")
        return
    mute.set_until(ts or None)
    ctx.send(mute_text(), inline=mute_buttons() if ts else None)


def _remove(rid: str) -> dict | None:
    with _lock:
        items = load()
        found = next((r for r in items if r["id"] == rid), None)
        save([r for r in items if r["id"] != rid])
    return found


def add_repeating(rid: str, text: str, every: int, days: int = 4) -> None:
    # a monthly task that pings in the daytime until "done" or until close(rid) (e.g. the file came)
    now = time.time()
    with _lock:
        items = [r for r in load() if r["id"] != rid]
        items.append(
            {"id": rid, "ts": now, "at": now, "text": text, "every": every, "give_up": days * 24, "day_only": True}
        )
        save(items)


def close(prefix: str) -> list:
    with _lock:
        items = load()
        gone = [r for r in items if r["id"].startswith(prefix)]
        save([r for r in items if not r["id"].startswith(prefix)])
    return gone


def panel_list() -> list:
    return [
        {"id": r["id"], "ts": int(r["ts"]), "text": r["text"], "every": r["every"]}
        for r in sorted(load(), key=lambda r: r["ts"])
    ]


def _edit(data: dict) -> dict | None:
    # from the panel: {"id", "text", "ts", "every"}; a new time starts the reminder over
    with _lock:
        items = load()
        for r in items:
            if r["id"] == data.get("id"):
                r["text"] = (data.get("text") or r["text"]).strip()
                r["every"] = max(0, int(data.get("every", r["every"])))
                if int(data.get("ts", r["ts"])) != int(r["ts"]):
                    r["ts"] = r["at"] = float(data["ts"])
                    r["pings"] = 0
                save(items)
                return r
    return None


def handle(ctx: Ctx, text: str) -> bool:
    if text.startswith("remedit:"):
        r = _edit(json.loads(text.split(":", 1)[1]))
        if r:
            ctx.send(f"✏️ Змінив: {_when(r['ts'])} — {r['text']} ({every_text(r['every'])})", menu=kb.MAIN)
        else:
            ctx.send("✏️ Не вийшло: такого нагадування вже немає.", menu=kb.MAIN)
    elif text.startswith("delrem:"):
        _remove(text.split(":", 1)[1])
        ctx.send("🗑 Видалив.\n\n" + text_list(), inline=buttons())
    elif text.startswith("remdone:"):
        r = _remove(text.split(":", 1)[1])
        ctx.send(f"✅ Зроблено: {r['text']}" if r else "✅ Це нагадування вже закрите.", menu=kb.MAIN)
    elif text.startswith("remsnooze:"):
        _, rid, *mins = text.split(":")
        mins = int(mins[0]) if mins and mins[0].isdigit() else SNOOZE_MIN
        with _lock:
            items = load()
            for r in items:
                if r["id"] == rid:
                    r["ts"] = time.time() + mins * 60
            save(items)
        ctx.send(f"💤 Нагадаю через {_mins(mins)}.", menu=kb.MAIN)
    elif m := MUTE_RE.match(text.strip()):
        _mute(ctx, (m.group(1) or "").strip())
    elif text == "clear_rem":
        with _lock:
            save([])
        ctx.send("🗑 Усі нагадування видалено.", menu=kb.MAIN)
    elif text == kb.REM:
        ctx.send("⏰ Нагадування", menu=kb.MAIN)
        ctx.send(text_list(), inline=buttons())
    elif text in (kb.REM_LIST, "reminders"):
        ctx.send(text_list(), inline=buttons())
    elif text == kb.REM_NEW:
        ctx.send(HELP, menu=kb.MAIN)
    elif rem := parse(text):
        with _lock:
            save(load() + [rem])
        ctx.send(f"👌 Нагадаю {_when(rem['ts'])}: {rem['text']} ({every_text(rem['every'])})", menu=kb.MAIN)
    elif REMIND_RE.match(text.strip()):
        ctx.send(
            "Не зрозумів час 🤔 Приклади:\nнагадай 20:00 подзвонити\nнагадай через 30 хв чайник\n"
            "нагадай завтра 9:00 зустріч кожні 5 хв",
            menu=kb.MAIN,
        )
    else:
        return False
    return True


def tick(ctx: Ctx) -> None:
    if sleepmode.held() or mute.active():  # held until "встав" or the end of the silence
        return
    now = time.time()
    with _lock:
        items = load()
        due, keep = [], []
        for r in items:
            if r["ts"] > now:
                keep.append(r)
                continue
            if r.get("day_only") and dt.datetime.now().hour not in DAY_HOURS:
                morning = dt.datetime.now().replace(hour=DAY_HOURS[0], minute=0, second=0)
                if morning.timestamp() < now:
                    morning += dt.timedelta(days=1)
                r["ts"] = morning.timestamp()
                keep.append(r)
                continue
            r["pings"] = r.get("pings", 0) + 1
            again = r["every"] and now - r.get("at", r["ts"]) < r.get("give_up", GIVE_UP_HOURS) * 3600
            if again:
                r["ts"] = now + r["every"] * 60
                keep.append(r)
            due.append((r, again))
        save(keep)
    for r, again in due:
        text = f"⏰ {r['text']}"
        if r["pings"] > 1:
            text += f" — нагадую ще раз ({r['pings']})"
        elif now - r.get("at", now) > 300:
            text += "\n(із запізненням — ноут був вимкнений)"
        ctx.send(text, inline=_ping_buttons(r) if again else None)
    for key, msg, every in planner.pending():
        if every:
            add_repeating(key, msg, every)
        else:
            ctx.send(msg)
        planner.mark(key)
