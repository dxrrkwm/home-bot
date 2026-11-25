import re
import time

from app import keyboards as kb
from app.telegram import Ctx

# button -> (lamp method, argument)
ACTIONS = {
    "💡 Увімкнути": ("power", True),
    "🌑 Вимкнути": ("power", False),
    "🔅 25%": ("bright", 25),
    "🔆 60%": ("bright", 60),
    "☀️ 100%": ("bright", 100),
    "🕯 Тепле": ("ct", 2700),
    "🌤 Денне": ("ct", 4000),
    "❄️ Холодне": ("ct", 6500),
    "🔴": ("rgb", 0xFF2000),
    "🟢": ("rgb", 0x00FF40),
    "🔵": ("rgb", 0x0040FF),
    "🟣": ("rgb", 0xA000FF),
    "🌙 Нічник": ("night", None),
}
# custom color and brightness as text: "#FF8C69", "#FF8C69 60%", "60%", "#f80 25%"
CUSTOM_RE = re.compile(r"^\s*(?:#([0-9a-f]{6}|[0-9a-f]{3}))?\s*(?:(\d{1,3})\s*%)?\s*$", re.I)
# white color temperature: "4000K", the lamp takes 1700-6500
CT_RE = re.compile(r"^\s*(\d{4})\s*[KkК]\s*$")
HINT = "🎨 Свій колір, яскравість чи температура — просто напиши: #FF8C69, #FF8C69 60%, 35% або 4000K"

_lamp = None


def _get():
    global _lamp
    if _lamp is None:
        from app.services import lamp  # needs cryptography, load only when used

        _lamp = lamp.robust()
    return _lamp


DOWN_SEC = 45  # after a failed try answer at once for a while: a search takes ~30 s and blocks other commands
_down_until = 0.0


def _with_lamp(fn) -> None:
    # lamp ip can change after outages, so on error reconnect once
    global _lamp, _down_until
    if time.time() < _down_until:
        raise RuntimeError("лампа недоступна — немає світла або Wi-Fi")
    for attempt in range(2):
        try:
            return fn(_get())
        except Exception:
            _lamp = None
            if attempt:
                _down_until = time.time() + DOWN_SEC
                raise


def _keep_visible(lamp) -> None:
    if (lamp.state()["bright"] or 0) < 30:
        lamp.bright(60)  # after night mode a color would be barely visible


def apply_action(text: str) -> None:
    method, arg = ACTIONS[text]

    def run(lamp):
        if method == "night":
            lamp.power(True)
            lamp.ct(1700)
            lamp.bright(1)
            return
        if method != "power":
            lamp.power(True)
        getattr(lamp, method)(arg)
        if method == "rgb":
            _keep_visible(lamp)

    _with_lamp(run)


def parse_custom(text: str) -> tuple[int | None, int | None] | None:
    m = CUSTOM_RE.match(text or "")
    if not m or not (m.group(1) or m.group(2)):
        return None
    rgb = None
    if m.group(1):
        h = m.group(1)
        rgb = int(h if len(h) == 6 else "".join(c * 2 for c in h), 16)
    bright = max(1, min(100, int(m.group(2)))) if m.group(2) else None
    return rgb, bright


def apply_custom(rgb: int | None, bright: int | None) -> None:
    def run(lamp):
        lamp.power(True)
        if rgb is not None:
            lamp.rgb(rgb)
        if bright is not None:
            lamp.bright(bright)
        elif rgb is not None:
            _keep_visible(lamp)

    _with_lamp(run)


def state() -> dict:
    # raw lamp state for the panel; rgb as "#RRGGBB" when the lamp is in color mode
    try:
        s = _with_lamp(lambda lamp: lamp.state())
    except Exception as e:
        return {"error": str(e)}
    rgb = f"#{s['rgb']:06X}" if s.get("rgb") is not None else None
    return {"on": s["on"], "bright": s["bright"], "rgb": rgb, "ct": None if rgb else s.get("ct")}


def status() -> str:
    try:
        s = _with_lamp(lambda lamp: lamp.state())
    except Exception as e:
        return f"💡 Лампа недоступна: {e}"
    if not s["on"]:
        return "💡 Лампа: вимкнена"
    tone = f"Колір: #{s['rgb']:06X}" if s.get("rgb") is not None else f"Температура: {s['ct']} K"
    return f"💡 Лампа: увімкнена\nЯскравість: {s['bright']}%\n{tone}"


def _do(ctx: Ctx, action) -> None:
    try:
        action()
        ctx.send(status(), menu=kb.MAIN)
    except Exception as e:
        ctx.send(f"💡 Не вийшло: {e}", menu=kb.MAIN)


def handle(ctx: Ctx, text: str) -> bool:
    custom = parse_custom(text) if text not in kb.BUTTONS else None
    if m := CT_RE.match(text):
        kelvin = int(m.group(1))
        _do(ctx, lambda: _with_lamp(lambda lamp: (lamp.power(True), lamp.ct(kelvin))))
    elif custom:
        _do(ctx, lambda: apply_custom(*custom))
    elif text == "lamp?":
        ctx.send(status())
    elif text == "toggle_light":
        _do(ctx, lambda: (_with_lamp(lambda lamp: lamp.toggle()), time.sleep(0.5)))
    elif text == kb.LIGHT:
        ctx.send(status() + "\n\n" + HINT, menu=kb.MAIN)
    elif text in ACTIONS:
        _do(ctx, lambda: apply_action(text))
    else:
        return False
    return True
