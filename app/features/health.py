# one look at every part of the bot: "/check", "перевірка" and the status screen of the panel
import platform
import time

from app import config, devices, link, relay, role
from app.features import light, money, presence, prices, station, system
from app.features import sleep as band_sleep
from app.services import mono, mute, outage, sleepmode
from app.services import sleep as cloud
from app.services import station as station_api

ICONS = {"ok": "🟢", "warn": "🟡", "bad": "🔴", "off": "⚪"}
ORDER = ["bad", "warn", "ok", "off"]


def _ago(sec: float) -> str:
    if sec < 90:
        return "щойно"
    if sec < 3600:
        return f"{int(sec // 60)} хв тому"
    if sec < 2 * 86400:
        return f"{int(sec // 3600)} год тому"
    return f"{int(sec // 86400)} дн тому"


def _item(key: str, name: str, state: str, text: str) -> dict:
    return {"k": key, "name": name, "state": state, "text": text}


def _bot() -> dict:
    where = f"{platform.node()} ({role.ROLE}), версія {system.version()}"
    if system.covering():
        return _item("bot", "Бот", "warn", f"підміняє хаб на ноуті · {where}")
    return _item("bot", "Бот", "ok", where)


def _relay() -> dict:
    if relay.connected():
        return _item("relay", "Панель і зв'язок", "ok", "MQTT на зв'язку")
    return _item("relay", "Панель і зв'язок", "bad", "немає з'єднання з MQTT — панель і ноут не відповідатимуть")


def _laptop() -> dict:
    s = devices.laptop_state()
    if not s:
        return _item("laptop", "Ноут", "warn", link.OFFLINE)
    mode = {"normal": "звичайний", "eco": "Еко", "min": "Мінімум"}[s.get("mode") or ("eco" if s["eco"] else "normal")]
    power = "від мережі" if s["ac"] else f"на батареї {s['pct']}%"
    return _item("laptop", "Ноут", "ok" if s["ac"] else "warn", f"{mode} · {power}")


def _lamp() -> dict:
    s = light.state()
    if s.get("error"):
        return _item("lamp", "Лампа", "bad", s["error"])
    return _item("lamp", "Лампа", "ok", f"увімкнена, {s.get('bright')}%" if s.get("on") else "вимкнена")


def _station() -> dict:
    if not station_api.configured():
        return _item("station", "Станція", "off", "не підключена")
    s = station.monitor.last
    if station.monitor.error:
        return _item("station", "Станція", "bad", station.monitor.error)
    if not s or not s.get("online"):
        return _item("station", "Станція", "bad", "офлайн — немає Wi-Fi або вимкнена")
    age = time.time() - s["ts"]
    if age > 600:
        return _item("station", "Станція", "warn", f"дані {_ago(age)}")
    return _item("station", "Станція", "ok", f"{s['pct']}% · {station_api.mode_of(s)} · {s['out_w']} Вт")


def _phone() -> dict:
    if not presence.enabled(config.load()):
        return _item("phone", "Пішов / прийшов", "off", "вимкнено")
    ago = devices.phone_ago()
    if ago is None:
        return _item("phone", "Пішов / прийшов", "warn", "не бачу телефон: ноут не на зв'язку або нема phone_mac")
    if ago < 120:
        return _item("phone", "Пішов / прийшов", "ok", "телефон удома")
    return _item("phone", "Пішов / прийшов", "ok", f"телефона немає в Wi-Fi {_ago(ago).replace(' тому', '')}")


def _mono() -> dict:
    if not mono.token():
        return _item("mono", "monobank", "off", "немає токена")
    age = money.age()
    err = money.last_error["text"]
    if err:
        return _item("mono", "monobank", "bad", f"помилка: {err}")
    if age > 3 * 3600:
        return _item("mono", "monobank", "warn", f"виписка {_ago(age)}")
    return _item("mono", "monobank", "ok", f"виписка {_ago(age)}")


def _band() -> dict:
    if not cloud.configured():
        return _item("band", "Браслет і сон", "off", "немає входу в Xiaomi")
    if err := band_sleep.last_error["text"]:
        return _item("band", "Браслет і сон", "bad", f"помилка: {err}")
    data = cloud.cached() or {}
    days = [d for d in data.get("days", []) if d.get("total")]
    last = days[-1]["day"] if days else None
    age = cloud.age()
    text = f"хмара {_ago(age)}" + (f" · останній сон {last[8:10]}.{last[5:7]}" if last else "")
    if age > 3 * 3600:
        return _item("band", "Браслет і сон", "warn", text)
    if last and time.time() - time.mktime(time.strptime(last, "%Y-%m-%d")) > 36 * 3600:
        return _item("band", "Браслет і сон", "warn", text + " — відкрий Mi Fitness, щоб залила сон")
    return _item("band", "Браслет і сон", "ok", text)


def _schedule() -> dict:
    if not outage.CACHE.exists():
        return _item("dtek", "Графік ДТЕК", "bad", "ще не завантажений")
    age = time.time() - outage.CACHE.stat().st_mtime
    return _item("dtek", "Графік ДТЕК", "ok" if age < 3600 else "warn", f"оновлено {_ago(age)}")


def _prices() -> dict:
    age = prices.prices.age()
    if age > 2 * prices.CHECK_SEC:
        return _item("prices", "Ціни на перекус", "warn", f"перевірено {_ago(age)}")
    return _item("prices", "Ціни на перекус", "ok", f"перевірено {_ago(age)}")


def _quiet() -> dict:
    if sleepmode.held():
        return _item("quiet", "Нагадування", "warn", "режим сну — чекають до «встав»")
    if ts := mute.until():
        return _item("quiet", "Нагадування", "warn", f"тиша до {time.strftime('%H:%M', time.localtime(ts))}")
    return _item("quiet", "Нагадування", "ok", "приходять як звичайно")


CHECKS = [_bot, _relay, _laptop, _lamp, _station, _phone, _mono, _band, _schedule, _prices, _quiet]


def collect() -> list:
    out = []
    for check in CHECKS:
        try:
            out.append(check())
        except Exception as e:
            out.append(_item(check.__name__.strip("_"), check.__name__.strip("_"), "bad", repr(e)))
    return out


def panel_data() -> dict:
    items = collect()
    worst = min((i["state"] for i in items), key=ORDER.index)
    return {"items": items, "worst": worst, "upd": time.strftime("%H:%M")}


def text() -> str:
    items = collect()
    bad = [i for i in items if i["state"] in ("bad", "warn")]
    head = "✅ Усе працює." if not bad else f"⚠️ Є на що глянути: {len(bad)}."
    return head + "\n\n" + "\n".join(f"{ICONS[i['state']]} {i['name']}: {i['text']}" for i in items)


def handle(ctx, text_in: str) -> bool:
    if text_in in ("/check", "перевірка", "проверка", "стан", "діагностика"):
        ctx.send(text())
        return True
    return False
