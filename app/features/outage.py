import re
import threading
import time

from app import config
from app import keyboards as kb
from app.features import station as st
from app.log import log
from app.services import outage, station
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


def _warning(key: str, mins: int, start_at, end_at, maybe: bool) -> str | None:
    end = "24:00" if end_at.date() > start_at.date() else end_at.strftime("%H:%M")
    when = f"{start_at:%H:%M}–{end}" + (" (можливо)" if maybe else "")
    s = st.monitor.last if station.configured() else None
    if st.monitor.ups:
        # the station takes over by itself, so one heads-up is enough
        if not key.startswith("h1:"):
            return None
        return f"⏰ За графіком через {mins} хв відключення: {when}." + (f" Станція {s['pct']}%." if s else "")
    if key.startswith("h1:"):
        msg = f"⏰ Через {mins} хв відключення: {when}."
        if s and s.get("online"):
            msg += f"\nСтанція {s['pct']}%" + (" — постав заряджатись, поки є час." if s["pct"] < 80 else " — готова.")
        return msg

    msg = f"⚠️ Через {mins} хв вимкнуть світло ({when}). Збережи гру/роботу."
    if not station.configured():
        return msg
    if not (s and s.get("online") and time.time() - s["ts"] < 600):
        return msg + "\n🔋 Увімкни станцію, натисни AC і підключи роутер та ноут."
    st.monitor.hold_ac_until = end_at.timestamp()
    try:
        if st.monitor.set_ac(True, "(перед відключенням)"):
            msg += "\n🔌 Увімкнув розетку на станції — можеш одразу переключити ноут на неї."
    except Exception as e:
        log(f"станція: {e!r}")
    return msg


def tick(ctx: Ctx) -> None:
    for key, mins, (start_at, end_at, maybe) in outage.due_warnings(ctx.cfg.get("outage_group")):
        if msg := _warning(key, mins, start_at, end_at, maybe):
            ctx.send(msg)
        outage.mark(key)
