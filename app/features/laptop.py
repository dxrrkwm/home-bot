import threading

from app import devices
from app import keyboards as kb
from app.link import OFFLINE
from app.services import sleepmode
from app.telegram import Ctx

NAMES = {"normal": "⚡ Звичайний", "eco": "🔋 Еко", "min": "🌑 Мінімум"}
AWAY_SEC = 5 * 60  # idle this long and then a touch: the user is back at the laptop

_idle = {"last": None}


def status_text() -> str:
    s = devices.laptop_state()
    if not s:
        return f"💻 {OFFLINE[0].upper()}{OFFLINE[1:]}."
    mode = NAMES[s.get("mode") or ("eco" if s["eco"] else "normal")]
    src = "🔌 від мережі/станції" if s["ac"] else "🪫 від батареї"
    bat = f"{s['pct']}%" if s["pct"] is not None else "—"
    return f"Режим: {mode}\nПлан Windows: {s['plan']}\nЕкран: {s['hz']} Гц\nЖивлення: {src}\nБатарея: {bat}"


def _mode(ctx: Ctx, mode: str, done: str) -> None:
    try:
        devices.run_mode(mode)
    except Exception as e:
        ctx.send(f"💻 Не вийшло: {e}")
        return
    ctx.send(done + "\n\n" + status_text())


def tick(ctx: Ctx) -> None:
    # min is for an empty room: the first touch after a while brings the usual mode back
    idle = devices.idle_sec()
    last, _idle["last"] = _idle["last"], idle
    if idle is None or last is None or last < AWAY_SEC or idle > 60:
        return
    if sleepmode.held() or devices.mode() != "min":
        return
    back = "normal" if devices.power()[0] else "eco"

    def run():
        if devices.switch(back):
            ctx.send(f"👋 З поверненням! Ноут знову: {NAMES[back]}.")

    threading.Thread(target=run, daemon=True).start()


def handle(ctx: Ctx, text: str) -> bool:
    if text in (kb.ECO, "eco"):
        _mode(ctx, "eco", "🔋 Еко увімкнено (Windows + OMEN Eco).")
    elif text in (kb.NORMAL, "normal"):
        _mode(ctx, "normal", "⚡ Звичайний режим (OMEN Performance).")
    elif text in (kb.MIN, "min"):
        _mode(
            ctx,
            "min",
            "🌑 Мінімум: екран гасне, CPU до 40%, лаунчери закрито, кришку можна закрити."
            "\nТоркнешся ноута — поверну звичайний.",
        )
    elif text in (kb.STATUS, "/status"):
        ctx.send(status_text())
    elif text == kb.PC:
        ctx.send("💻 ПК\n\n" + status_text(), menu=kb.MAIN)
    else:
        return False
    return True
