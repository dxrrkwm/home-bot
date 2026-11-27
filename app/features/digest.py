# the week in one message on sunday evening: sleep, steps, money and goals, outages, work. "підсумок" any time
import datetime as dt
import json

from app import config
from app.features import band, money
from app.features import sleep as band_sleep
from app.paths import ROOT
from app.services import mono, powerlog, shifts
from app.services import sleep as cloud
from app.telegram import Ctx

STATE = ROOT / "digest_state.json"
SEND_AT = (6, 20)  # sunday 20:00


def _sleep() -> str | None:
    data = cloud.cached()
    avg = band_sleep._avg(data) if data else None
    if not avg:
        return None
    line = f"😴 Сон: у середньому {band_sleep.hm(avg)} на добу"
    if d := band.debt():
        line += f", недосип {band_sleep.hm(d[0])}" if d[0] else ", висипаєшся"
    return line + "."


def _steps() -> str | None:
    a = band.activity()
    if not a or not a["avg7"]:
        return None
    return f"👣 Кроки: ≈ {money.uah(a['avg7'])} на день, {str(a['km7']).replace('.', ',')} км за тиждень."


def _money(cfg: dict) -> str | None:
    data = mono.panel_data(money.budget(cfg))
    if not data:
        return None
    week = sum(v for _, v in data["days"][-7:])
    budget = data["budget"] * 7
    diff = budget - week
    line = f"💰 Витрати: {money.uah(week)} грн з {money.uah(budget)}"
    line += f" — зекономив {money.uah(diff)}." if diff >= 0 else f" — понад на {money.uah(-diff)}."
    if goals := mono.goals_text(mono.saved_goals(money.deadlines(cfg))):
        line += "\n" + goals
    return line


def _power() -> str | None:
    s = powerlog.stats(7)
    if not s:
        return None
    if not s["outages"]:
        return "💡 Відключень не було."
    hours = sum(b - a for a, b in s["outages"]) / 3600
    return f"💡 Без світла: {len(s['outages'])} раз(и), разом {hours:.1f} год.".replace(".", ",", 1)


def _work() -> str | None:
    end = dt.datetime.now()
    start = end - dt.timedelta(days=7)
    hours = sum(
        (min(b, end) - max(a, start)).total_seconds() / 3600
        for a, b, *_ in shifts.all_shifts()
        if a < end and b > start
    )
    return f"💼 Робота: ≈ {hours:.0f} год змін." if hours else None


def text(cfg: dict) -> str:
    parts = [p for p in (_sleep(), _steps(), _money(cfg), _power(), _work()) if p]
    return "📅 Тиждень коротко:\n\n" + ("\n\n".join(parts) if parts else "даних поки немає.")


def handle(ctx: Ctx, text_in: str) -> bool:
    if text_in in ("підсумок", "итоги", "/week"):
        ctx.send(text(ctx.cfg))
        return True
    return False


def tick(ctx: Ctx) -> None:
    now = dt.datetime.now()
    if (now.weekday(), now.hour) != SEND_AT:
        return
    week = now.strftime("%G-%V")
    try:
        sent = json.loads(STATE.read_text(encoding="utf-8")).get("week")
    except Exception:
        sent = None
    if sent == week:
        return
    STATE.write_text(json.dumps({"week": week}), encoding="utf-8")
    ctx.send(text(config.load()))
