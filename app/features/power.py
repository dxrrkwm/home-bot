# reacts to the laptop switching between mains and battery, which usually means a power outage
import socket
import threading
import time

from app import devices
from app.features import station as st
from app.log import log
from app.services import powerlog, station
from app.telegram import Ctx

SAMPLE_SEC = 60
PLUG_WAIT_SEC = 4 * 60
STALE_SEC = 180  # older station data is not a sample of now

_state = {"ac": None, "low_sent": False, "sampled": 0, "net": None}


def _internet() -> bool:
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=3).close()
        return True
    except OSError:
        return False


def _sample(ac: bool, pct: int | None) -> None:
    net = _internet()
    if net != _state["net"]:
        if _state["net"] is not None:
            powerlog.event("net_up" if net else "net_down")
        _state["net"] = net
    s = st.monitor.last
    fresh = bool(s) and time.time() - s["ts"] < STALE_SEC
    fields = {
        "laptop_ac": None if ac is None else int(ac),
        "laptop_pct": pct,
        "eco": int(devices.is_eco()),
        "net": int(net),
        "ups": int(st.monitor.ups),
    }
    if fresh:
        fields |= {
            "station_online": int(s["online"]),
            "pct": s["pct"],
            "mode": station.mode_of(s),
            "ac_on": int(bool(s.get("ac_on"))),
            "ac_in": s["ac_in"],
            "dc_in": s["dc_in"],
            "in_w": s["in_w"],
            "out_w": s["out_w"],
            "temp": s.get("temp"),
            "inv_temp": s.get("inv_temp"),
        }
    powerlog.sample(**fields)


def on_ac_lost(ctx: Ctx, pct: int | None) -> None:
    if st.monitor.ups:
        # the station should keep the laptop powered, so this means it does not
        ctx.send(
            f"🪫 Ноут перейшов на батарею ({pct}%) — станція його не живить. "
            "Глянь, чи увімкнена станція і розетка AC.",
            inline=[[{"text": "🔌 Увімкнути AC", "callback_data": "ac:on"}]],
        )
        return
    msg = f"⚫ Ноут перейшов на батарею ({pct}%). Світло зникло?"
    inline = None
    if ctx.cfg.get("station_auto_eco", True) and not devices.is_eco() and devices.switch("eco"):
        msg += "\n🔋 Увімкнув Еко."
        inline = [[{"text": "⚡ Повернути звичайний", "callback_data": "normal"}]]
    if station.configured():
        st.monitor.hold_ac_until = time.time() + 30 * 60
        try:
            if st.monitor.set_ac(True, "(світло зникло)"):
                msg += "\n🔌 Розетку на станції увімкнув."
        except Exception as e:  # no internet means no station cloud
            log(f"станція: {e!r}")
        msg += "\n" + st.brief(st.monitor.last) + " Підключи ноут до станції."
    ctx.send(msg, inline=inline)


def _station_after_plug() -> dict | None:
    # router and station need a minute or two after being plugged in, and the station
    # reports the new load with a delay, so wait until it says supply or charging
    deadline = time.time() + PLUG_WAIT_SEC
    while time.time() < deadline:
        time.sleep(20)
        s = st.monitor.fresh(max_age=5)
        if s and s.get("online") and station.mode_of(s) != "idle":
            return s
    return None


def on_ac_back(ctx: Ctx) -> None:
    if st.monitor.ups:
        ctx.send("🔌 Ноут знову живиться.")
        return
    # either power is back or the laptop was plugged into the station
    s = _station_after_plug() if station.configured() else None
    if station.configured() and s is None and devices.is_eco():
        ctx.send(
            "🔌 Ноут знову живиться, але станція не відповідає — лишаю Еко.",
            inline=[[{"text": "⚡ Звичайний", "callback_data": "normal"}]],
        )
        return
    if s and station.mode_of(s) == "supply":
        inline = None if devices.is_eco() else [[{"text": "🔋 Увімкнути еко", "callback_data": "eco"}]]
        ctx.send(f"🔌 Ноут живиться від станції. {st.brief(s)}", inline=inline)
        return
    msg = "💡 Ноут знову від розетки — схоже, світло дали."
    inline = None
    if ctx.cfg.get("station_auto_eco", True) and devices.mode() == "eco" and devices.switch("normal"):
        msg += "\n⚡ Повернув звичайний режим."
        inline = [[{"text": "🔋 Лишити еко", "callback_data": "eco"}]]
    if s and s.get("online") and s["pct"] < 90 and station.mode_of(s) != "charging":
        msg += f"\n🔋 Станція {s['pct']}% — постав її на зарядку."
    ctx.send(msg, inline=inline)


def tick(ctx: Ctx) -> None:
    ac, pct = devices.power()
    if time.time() - _state["sampled"] >= SAMPLE_SEC:
        _state["sampled"] = time.time()
        threading.Thread(target=_sample, args=(ac, pct), daemon=True, name="power_sample").start()
    if ac is None:  # laptop out of reach: nothing to compare
        return
    if _state["ac"] is None:
        _state["ac"] = ac
    if not ac and pct is not None and pct <= 20 and not _state["low_sent"]:
        ctx.send(
            f"🪫 Батарея ноутбука {pct}%! Підключи станцію або збережи роботу.",
            inline=[[{"text": "🔋 Увімкнути еко", "callback_data": "eco"}]],
        )
        _state["low_sent"] = True
    if ac:
        _state["low_sent"] = False
    if ac != _state["ac"]:
        _state["ac"] = ac
        powerlog.event("laptop_mains" if ac else "laptop_battery", f"{pct}%")
        if not ac:
            on_ac_lost(ctx, pct)
        else:
            threading.Thread(target=on_ac_back, args=(ctx,), daemon=True, name="ac_back").start()
