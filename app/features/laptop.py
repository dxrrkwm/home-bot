from app import devices
from app import keyboards as kb
from app.link import OFFLINE
from app.telegram import Ctx


def status_text() -> str:
    s = devices.laptop_state()
    if not s:
        return f"💻 {OFFLINE[0].upper()}{OFFLINE[1:]}."
    mode = "🔋 Еко" if s["eco"] else "⚡ Звичайний"
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


def handle(ctx: Ctx, text: str) -> bool:
    if text in (kb.ECO, "eco"):
        _mode(ctx, "eco", "🔋 Еко увімкнено (Windows + OMEN Eco).")
    elif text in (kb.NORMAL, "normal"):
        _mode(ctx, "normal", "⚡ Звичайний режим (OMEN Performance).")
    elif text in (kb.STATUS, "/status"):
        ctx.send(status_text())
    elif text == kb.PC:
        ctx.send("💻 ПК\n\n" + status_text(), menu=kb.MAIN)
    else:
        return False
    return True
