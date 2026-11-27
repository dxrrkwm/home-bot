# the phone left the home wi-fi: the laptop goes to min and the lamp off; back home: all of it as it was.
# "вдома вимк" / "вдома увімк" switches it off and on
import datetime as dt
import json
import threading
import time

from app import config, devices
from app.features import laptop, light
from app.log import log
from app.paths import ROOT
from app.services import sleepmode
from app.telegram import Ctx

STATE = ROOT / "presence_state.json"
AWAY_SEC = 10 * 60  # android drops off the wi-fi for a few minutes now and then
BACK_SEC = 90

_busy = threading.Lock()


def _load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(s: dict) -> None:
    STATE.write_text(json.dumps(s), encoding="utf-8")


def enabled(cfg: dict) -> bool:
    return cfg.get("presence", True)


def status() -> str:
    s, ago = _load(), devices.phone_ago()
    if not enabled(config.load()):
        return "🏠 Автоматика «пішов / прийшов» вимкнена. Увімкнути: вдома увімк"
    if ago is None:
        return "🏠 Не бачу, чи телефон удома: ноут не на зв'язку або не вказаний phone_mac."
    if s.get("away"):
        return f"🚶 Тебе немає вдома з {dt.datetime.fromtimestamp(s['since']):%H:%M}."
    return "🏠 Ти вдома."


def _leave(ctx: Ctx) -> None:
    lamp_on = False
    try:
        lamp_on = bool(light.with_lamp(lambda lamp: lamp.state()).get("on"))
        if lamp_on:
            light.with_lamp(lambda lamp: lamp.power(False))
    except Exception as e:
        log(f"пішов, лампа: {e!r}")
    to_min = devices.mode() != "min" and devices.switch("min")
    _save({"away": True, "since": time.time(), "lamp": lamp_on, "min": to_min})
    done = [x for x, ok in (("лампу вимкнув", lamp_on), ("ноут у «Мінімумі»", to_min)) if ok]
    if done:
        ctx.send("🚶 Телефон пішов з дому — " + ", ".join(done) + ".")


def _back(ctx: Ctx) -> None:
    s = _load()
    _save({"away": False, "since": time.time()})
    done = []
    if s.get("min") and devices.mode() == "min":
        back = "normal" if devices.power()[0] else "eco"
        if devices.switch(back):
            done.append(f"ноут: {laptop.NAMES[back]}")
    if s.get("lamp"):
        try:
            light.with_lamp(lambda lamp: lamp.power(True))
            done.append("лампу ввімкнув")
        except Exception as e:
            log(f"прийшов, лампа: {e!r}")
    if done:
        ctx.send("🏠 З поверненням! " + ", ".join(done) + ".")


def _run(fn, ctx: Ctx) -> None:
    # the lamp and the laptop are slow: not in the main loop, one change at a time
    def go():
        with _busy:
            fn(ctx)

    threading.Thread(target=go, daemon=True).start()


def handle(ctx: Ctx, text: str) -> bool:
    if text in ("вдома", "дома", "/home"):
        ctx.send(status())
    elif text in ("вдома вимк", "дома выкл"):
        ctx.cfg["presence"] = False
        config.save(ctx.cfg)
        ctx.send("🏠 Добре, більше не стежу, чи ти вдома.")
    elif text in ("вдома увімк", "дома вкл"):
        ctx.cfg["presence"] = True
        config.save(ctx.cfg)
        ctx.send("🏠 Стежу: підеш — ноут у «Мінімум» і лампу вимкну, прийдеш — поверну як було.")
    else:
        return False
    return True


def tick(ctx: Ctx) -> None:
    ago = devices.phone_ago()
    if ago is None or not enabled(ctx.cfg) or _busy.locked():
        return
    away = _load().get("away", False)
    if not away and ago > AWAY_SEC:
        # asleep the phone may drop off the wi-fi, and somebody at the laptop is at home anyway
        idle = devices.idle_sec()
        if sleepmode.active() or (idle is not None and idle < AWAY_SEC):
            return
        _run(_leave, ctx)
    elif away and ago < BACK_SEC:
        _run(_back, ctx)
