import json
import re
import time

from app import keyboards as kb
from app.paths import REMINDERS
from app.services import planner
from app.telegram import Ctx

REMIND_RE = re.compile(r"^(?:нагадай|напомни|remind)\s+(.*)$", re.I | re.S)
IN_RE = re.compile(r"^через\s+(\d+)\s*(хв|мин|м|min|год|час|ч|г|h)\w*\.?\s*(.*)$", re.I | re.S)
AT_RE = re.compile(r"^(?:о\s+|в\s+|на\s+)?(завтра\s+)?(\d{1,2})[:.](\d{2})\s*(.*)$", re.I | re.S)

HELP = (
    "➕ Щоб додати нагадування, просто напиши:\n"
    "нагадай 20:00 подзвонити\nнагадай через 30 хв зняти чайник\nнагадай завтра 9:00 зустріч\n\n"
    "💊 Таблетку:\nтаблетка Назва 09:00\nтаблетка Назва 09:00, 21:00\nтаблетка Назва 10:00 кожні 2 дні"
)


def load() -> list:
    try:
        return json.loads(REMINDERS.read_text(encoding="utf-8"))
    except Exception:
        return []


def save(items: list) -> None:
    REMINDERS.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def parse(text: str) -> tuple[float, str] | None:
    # "нагадай 20:00 текст" / "нагадай через 30 хв текст" / "нагадай завтра 9:00 текст"
    m = REMIND_RE.match(text.strip())
    if not m:
        return None
    rest = m.group(1).strip()
    now = time.time()
    if m2 := IN_RE.match(rest):
        n, unit, what = int(m2.group(1)), m2.group(2).lower(), m2.group(3)
        return now + n * (3600 if unit[0] in "гчh" else 60), what.strip() or "⏰"
    if m2 := AT_RE.match(rest):
        tomorrow, h, mi, what = m2.group(1), int(m2.group(2)), int(m2.group(3)), m2.group(4)
        if h > 23 or mi > 59:
            return None
        t = time.localtime(now)
        ts = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, h, mi, 0, 0, 0, -1))
        if tomorrow or ts <= now:
            ts += 86400
        return ts, what.strip() or "⏰"
    return None


def _when(ts: float) -> str:
    return time.strftime("%d.%m %H:%M", time.localtime(ts))


def text_list() -> str:
    items = sorted(load(), key=lambda r: r["ts"])
    if not items:
        return "⏰ Нагадувань немає.\n\n" + HELP
    return (
        "⏰ Нагадування:\n"
        + "\n".join(f"{_when(r['ts'])} — {r['text']}" for r in items)
        + "\n\nВидалити — кнопкою нижче."
    )


def buttons() -> list | None:
    items = sorted(load(), key=lambda r: r["ts"])
    rows = [
        [{"text": f"❌ {_when(r['ts'])} {r['text']}"[:48], "callback_data": f"delrem:{r['ts']:.0f}"}] for r in items
    ]
    if len(items) > 1:
        rows.append([{"text": "🗑 Видалити всі", "callback_data": "clear_rem"}])
    return rows or None


def handle(ctx: Ctx, text: str) -> bool:
    if text.startswith("delrem:"):
        ts = text.split(":", 1)[1]
        save([r for r in load() if f"{r['ts']:.0f}" != ts])
        ctx.send("🗑 Видалив.\n\n" + text_list(), inline=buttons())
    elif text == "clear_rem":
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
        ts, what = rem
        save(load() + [{"ts": ts, "text": what}])
        ctx.send(f"👌 Нагадаю {time.strftime('%d.%m о %H:%M', time.localtime(ts))}: {what}", menu=kb.MAIN)
    elif REMIND_RE.match(text.strip()):
        ctx.send(
            "Не зрозумів час 🤔 Приклади:\nнагадай 20:00 подзвонити\nнагадай через 30 хв чайник\n"
            "нагадай завтра 9:00 зустріч",
            menu=kb.MAIN,
        )
    else:
        return False
    return True


def tick(ctx: Ctx) -> None:
    items = load()
    due = [r for r in items if r["ts"] <= time.time()]
    for r in due:
        late = time.time() - r["ts"] > 300
        ctx.send(f"⏰ {r['text']}" + ("\n(із запізненням — ноут був вимкнений)" if late else ""))
    if due:
        save([r for r in items if r["ts"] > time.time()])
    for key, msg in planner.pending():
        ctx.send(msg)
        planner.mark(key)
