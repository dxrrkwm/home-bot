import re
import threading
import time

from app import config, devices
from app import keyboards as kb
from app.features import station as st
from app.log import log
from app.services import outage, shifts, station
from app.telegram import Ctx

GROUP_RE = re.compile(r"^\s*(?:черга|очередь|група|группа)\s*(\d)\s*[.,]\s*(\d)\s*$", re.I)


def _loop() -> None:
    while True:
        try:
            outage.fetch()
        except Exception as e:
            log(f"графік: {e!r}")
        time.sleep(60)


def start() -> None:
    threading.Thread(target=_loop, daemon=True, name="outage").start()


def handle(ctx: Ctx, text: str) -> bool:
    if text == "sched":
        ctx.send(outage.schedule_text(ctx.cfg.get("outage_group")), menu=kb.MAIN)
        return True
    m = GROUP_RE.match(text)
    if m:
        ctx.cfg["outage_group"] = f"{m.group(1)}.{m.group(2)}"
        config.save(ctx.cfg)
        ctx.send("👌 Запам'ятав чергу.", menu=kb.MAIN)
        ctx.send(outage.schedule_text(ctx.cfg["outage_group"]), menu=kb.MAIN)
        return True
    return False


MIN_WATTS = 27  # laptop in min and the router, measured 2026-11-27
MIN_BUTTON = [[{"text": "🌑 Ноут у «Мінімум»", "callback_data": kb.MIN}]]


def _enough(s: dict | None, start_at, end_at) -> tuple[str, list | None]:
    # will the station hold out the whole outage with the load it has now, and in min
    left = st.runtime(s)
    if not left:
        return "", None
    need = int((end_at - start_at).total_seconds() // 60)
    text = f"\n🔋 Станція {s['pct']}%, вистачить ≈ {station.fmt_mins(left)} — "
    if left >= need * 1.15:
        return text + "з запасом.", None
    in_min = st.runtime(s, watts=MIN_WATTS)
    if devices.mode() != "min" and in_min and in_min > left:
        more = "вистачить" if in_min >= need else f"протягне ≈ {station.fmt_mins(in_min)}"
        return text + f"на все відключення ({station.fmt_mins(need)}) не вистачить. У «Мінімумі» {more}.", MIN_BUTTON
    return text + f"на все відключення ({station.fmt_mins(need)}) не вистачить.", None


def _warning(key: str, mins: int, start_at, end_at, maybe: bool) -> tuple[str, list | None] | None:
    end = "24:00" if end_at.date() > start_at.date() else end_at.strftime("%H:%M")
    when = f"{start_at:%H:%M}–{end}" + (" (можливо)" if maybe else "")
    s = st.monitor.last if station.configured() else None
    if st.monitor.ups:
        # the station takes over by itself, so one heads-up is enough
        if not key.startswith("h1:"):
            return None
        extra, inline = _enough(s, start_at, end_at)
        return f"⏰ За графіком через {mins} хв відключення: {when}." + extra, inline
    if key.startswith("h1:"):
        msg = f"⏰ Через {mins} хв відключення: {when}."
        if s and s.get("online") and s["pct"] < 80:
            return msg + f"\nСтанція {s['pct']}% — постав заряджатись, поки є час.", None
        extra, inline = _enough(s, start_at, end_at)
        return msg + extra, inline

    msg = f"⚠️ Через {mins} хв вимкнуть світло ({when}). Збережи гру/роботу."
    if not station.configured():
        return msg, None
    if not (s and s.get("online") and time.time() - s["ts"] < 600):
        return msg + "\n🔋 Увімкни станцію, натисни AC і підключи роутер та ноут.", None
    st.monitor.hold_ac_until = end_at.timestamp()
    try:
        if st.monitor.set_ac(True, "(перед відключенням)"):
            msg += "\n🔌 Увімкнув розетку на станції — можеш одразу переключити ноут на неї."
    except Exception as e:
        log(f"станція: {e!r}")
    return msg, None


def _on_shift(start_at, end_at) -> str:
    # work goes through the internet: an outage during a shift is worth a second look
    hit = next((s for s in shifts.all_shifts() if s[0] < end_at and start_at < s[1]), None)
    return f"\n💼 Це під час твоєї зміни ({hit[0]:%H:%M}–{hit[1]:%H:%M}) — глянь заряд станції." if hit else ""


def tick(ctx: Ctx) -> None:
    for key, mins, (start_at, end_at, maybe) in outage.due_warnings(ctx.cfg.get("outage_group")):
        if warn := _warning(key, mins, start_at, end_at, maybe):
            ctx.send(warn[0] + _on_shift(start_at, end_at), inline=warn[1])
        outage.mark(key)
