import datetime as dt
import threading
import time

from app import config, devices, relay
from app import keyboards as kb
from app.log import log
from app.services import outage, powerlog, station
from app.telegram import Ctx

WEEKLY_AT = (0, 10)  # monday 10:00
PUSH_SEC = 60  # station state to an open panel
LED = {"led:0": 0, "led:1": 1, "led:2": 2}  # station light; 3 (sos) is left out on purpose
LED_NAMES = {0: "вимкнено", 1: "світить", 2: "блимає"}

monitor = station.Monitor(log=log)


def start() -> None:
    monitor.ups = bool(config.load().get("station_ups"))
    if not any(f.startswith("week:") for f in monitor.flags):  # first run: start counting from next week
        monitor.flags.add(f"week:{time.strftime('%G-%V')}")
    monitor.start()


def status() -> str:
    if not station.configured():
        return (
            "🔌 Станція ще не підключена.\n"
            f"Відкрий {station.ENV} і заміни обидва ВСТАВ_СЮДИ на логін і пароль від апки Wonderfree. "
            "Бот підхопить сам за хвилину."
        )
    text = station.status_text(monitor.last)
    if monitor.error:
        text += f"\n⚠️ Помилка: {monitor.error}"
    return text


def brief(s: dict | None) -> str:
    # one line about the station for power messages
    if not s or not s.get("online"):
        return "Станція: немає зв'язку."
    text = f"Станція: {s['pct']}%"
    if s.get("mins_left"):
        text += f", вистачить ≈ {station.fmt_mins(s['mins_left'])}"
    return text + "."


def _grid(s: dict | None, laptop_ac: bool) -> bool | None:
    if monitor.ups:
        # the laptop never leaves mains in ups mode, only the station knows
        return monitor.grid if s and s.get("online") else None
    return laptop_ac and not (s and s.get("online") and station.mode_of(s) == "supply")


def _day_segments(intervals: list, day: dt.date) -> list[list[int]]:
    # [start, end] in minutes from midnight of this day
    start = dt.datetime.combine(day, dt.time())
    out = []
    for a, b in intervals:
        a, b = max(a, start), min(b, start + dt.timedelta(days=1))
        if a < b:
            out.append([int((a - start).total_seconds() // 60), int((b - start).total_seconds() // 60)])
    return out


def _day_name(t: dt.datetime) -> str:
    days = (t.date() - dt.date.today()).days
    return {0: "Сьогодні", 1: "Завтра"}.get(days, t.strftime("%d.%m"))


def _hm_end(a: dt.datetime, b: dt.datetime) -> str:
    return "24:00" if b.date() > a.date() and b.time() == dt.time() else b.strftime("%H:%M")


def panel_data(cfg: dict, force: bool = False) -> dict:
    s = monitor.fresh(max_age=0 if force else 30) if station.configured() else None
    ac, lpct = devices.power()
    stats = powerlog.stats(7) or {"outages": [], "supply": None, "charging": None}
    fact = [(dt.datetime.fromtimestamp(a), dt.datetime.fromtimestamp(b)) for a, b in stats["outages"]]
    plan = [(a, b) for a, b, _ in outage.intervals(cfg.get("outage_group"))]
    today = dt.date.today()
    days = []
    for i in range(7):
        day = today - dt.timedelta(days=i)
        days.append(
            {
                "d": day.strftime("%d.%m"),
                "w": day.weekday(),
                "off": _day_segments(fact, day),
                "plan": _day_segments(plan, day),
            }
        )

    grid = _grid(s, ac)
    since = None
    if grid is False:
        if monitor.outage_start:
            since = monitor.outage_start[0]
        elif stats["outages"]:
            since = stats["outages"][-1][0]
    drain = stats["supply"][0] if stats["supply"] else None
    est = None
    if s and drain:
        est = s.get("mins_left") or int(max(s["pct"] - 5, 0) / drain * 60)
    upcoming = [(a, b) for a, b in plan if b > dt.datetime.now()]
    return {
        "upd": time.strftime("%H:%M"),
        "configured": station.configured(),
        "error": monitor.error,
        "station": s
        and {
            "online": s["online"],
            "age": int(time.time() - s["ts"]),
            "pct": s["pct"],
            "mode": station.mode_of(s),
            "in": s["in_w"],
            "out": s["out_w"],
            "ac": bool(s.get("ac_on")),
            "led": s.get("led", 0),
            "temp": s.get("temp"),
            "charge": s.get("charge_mins"),
        },
        "est": est,
        "ups": monitor.ups,
        "grid": grid,
        "since": since,
        "laptop": {"ac": ac, "pct": lpct, "eco": devices.is_eco()},
        "days": days,
        "count": len(stats["outages"]),
        "hours": round(sum(b - a for a, b in stats["outages"]) / 3600, 1),
        "drain": drain and round(drain, 1),
        "charge_rate": stats["charging"] and round(stats["charging"][0]),
        "hist": powerlog.battery(24),
        "plan": [[_day_name(a), a.strftime("%H:%M"), _hm_end(a, b)] for a, b in upcoming[:6]],
        "group": cfg.get("outage_group"),
    }


def _grid_lost(ctx: Ctx, s: dict) -> None:
    msg = f"⚫ Світло зникло, все працює від станції. {brief(s)}"
    inline = None
    if ctx.cfg.get("station_auto_eco", True) and not devices.is_eco() and devices.switch("eco"):
        msg += "\n🔋 Увімкнув Еко."
        inline = [[{"text": "⚡ Повернути звичайний", "callback_data": "normal"}]]
    ctx.send(msg, inline=inline)


def _grid_back(ctx: Ctx, s: dict) -> None:
    msg = "💡 Світло є, станція знову від мережі."
    if start := s.get("outage_start"):
        used = max(start[1] - s["pct"], 0)
        spent = f"витрачено {used}%" if used else "заряд не змінився"
        msg += f" Без світла було {station.fmt_mins((time.time() - start[0]) / 60)}, станція {s['pct']}% ({spent})."
    inline = None
    if ctx.cfg.get("station_auto_eco", True) and devices.is_eco() and devices.switch("normal"):
        msg += "\n⚡ Повернув звичайний режим."
        inline = [[{"text": "🔋 Лишити еко", "callback_data": "eco"}]]
    ctx.send(msg, inline=inline)


def _weekly(ctx: Ctx) -> None:
    now = time.localtime()
    week = f"week:{time.strftime('%G-%V', now)}"
    if (now.tm_wday, now.tm_hour) < WEEKLY_AT or week in monitor.flags:
        return
    monitor.flags = {f for f in monitor.flags if not f.startswith("week:")} | {week}
    ctx.send(
        powerlog.summary(7) + "\n\n📨 Перешли цей підсумок асистенту — розберемо тиждень і підкрутимо налаштування."
    )


def buttons() -> list:
    return [
        [{"text": "🔌 AC увімкнути", "callback_data": "ac:on"}, {"text": "⭕ AC вимкнути", "callback_data": "ac:off"}],
        [
            {"text": "🗓 Графік відключень", "callback_data": "sched"},
            {"text": "📊 Статистика", "callback_data": "station_stats"},
        ],
    ]


def handle(ctx: Ctx, text: str) -> bool:
    if text in ("ac:on", "ac:off"):
        on = text == "ac:on"
        try:
            if on:
                monitor.hold_ac_until = time.time() + 30 * 60  # time to plug something in
            monitor.set_ac(on, "(вручну)")
            ctx.send(f"🔌 Розетку AC {'увімкнено' if on else 'вимкнено'}.", menu=kb.MAIN)
        except Exception as e:
            ctx.send(f"🔌 Не вийшло: {e}", menu=kb.MAIN)
        return True
    if text in LED or text == "led:toggle":
        mode = LED.get(text, 0 if (monitor.last or {}).get("led") else 1)
        try:
            monitor.set_led(mode)
            ctx.send(f"🔦 Ліхтар на станції: {LED_NAMES[mode]}.", menu=kb.MAIN)
        except Exception as e:
            ctx.send(f"🔦 Не вийшло: {e}", menu=kb.MAIN)
        return True
    if text == "station_stats":
        ctx.send(powerlog.summary(7), menu=kb.MAIN)
        return True
    if text.lower() in ("ups on", "ups off"):
        ctx.cfg["station_ups"] = monitor.ups = text.lower() == "ups on"
        config.save(ctx.cfg)
        if monitor.ups:
            ctx.send("🔌 Режим UPS: станція в розетці, роутер і ноут через неї. Світло бачу по станції.", menu=kb.MAIN)
        else:
            ctx.send("🔌 Режим UPS вимкнено: світло знову бачу по ноуту.", menu=kb.MAIN)
        return True
    if text in (kb.STATION, "station"):
        ctx.send(status(), inline=buttons() if station.configured() else None, menu=kb.MAIN)
        return True
    return False


_pushed = {"at": 0.0}


def _push(cfg: dict) -> None:
    # an open panel gets the station state by itself; only over mqtt, ntfy has a daily limit
    if time.time() - _pushed["at"] < PUSH_SEC or not relay.mqtt_connected():
        return
    _pushed["at"] = time.time()
    threading.Thread(target=lambda: relay.publish("station", panel_data(cfg)), daemon=True).start()


def tick(ctx: Ctx) -> None:
    monitor.ups = bool(ctx.cfg.get("station_ups"))
    _weekly(ctx)
    _push(ctx.cfg)
    for kind, s in monitor.pop_events():
        if kind == "grid_lost":
            _grid_lost(ctx, s)
        elif kind == "grid_back":
            _grid_back(ctx, s)
        elif kind == "hot":
            ctx.send(
                f"🌡 Станція гріється: {s.get('temp')}° / інвертор {s.get('inv_temp')}°. Перевір, щоб нічим не була накрита."
            )
        elif kind.startswith("fault:"):
            ctx.send(f"⚠️ Станція показує помилку (код {s.get('fault')}). Глянь екран станції або Wonderfree.")
        elif kind == "offline":
            ctx.send("📡 Станція вже 10 хв не на зв'язку. Глянь, чи вона увімкнена.")
        elif kind == "ac_off_ups":
            ctx.send(
                "🔌 Розетка AC на станції вимкнена — роутер і ноут не під захистом.",
                inline=[[{"text": "🔌 Увімкнути AC", "callback_data": "ac:on"}]],
            )
        elif kind.startswith("low:"):
            icon = "🟥" if s["pct"] <= 15 else "🟧"
            ctx.send(
                f"{icon} Станція: {s['pct']}%! Вистачить ≈ {station.fmt_mins(s.get('mins_left'))}. "
                "Збережи роботу, вимкни зайве."
            )
        elif kind == "full":
            ctx.send("✅ Станція заряджена на 100%. Можна вимикати з розетки.")
        elif kind == "charge_hint":
            if devices.power()[0]:  # laptop on mains means there is power
                ctx.send(f"🔋 Станція {s['pct']}%. Поки є світло — постав її заряджатись.")
            else:
                monitor.flags.discard("charge_hint")  # remind when power is back
