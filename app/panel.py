# telegram mini app (github.com/dxrrkwm/home-bot-panel): commands and answers go through app.relay
import threading

from app import config, keyboards, relay, router, telegram
from app.features import light, money, prices, reminders, sleep, station
from app.log import log
from app.services import mono

FIN_STALE_SEC = 15 * 60
FIN_FORCE_SEC = 60  # monobank gives a statement once a minute

publish = relay.publish


def publish_finance() -> None:
    data = mono.panel_data(config.load().get("daily_budget", 280))
    if data:
        data["syncing"] = mono.is_syncing()
        publish("fin", data)


def _finance(force: bool = False) -> None:
    if mono.token() and not mono.is_syncing() and money.age() > (FIN_FORCE_SEC if force else FIN_STALE_SEC):
        mono.sync_in_background(done=lambda err: publish_finance())
    publish_finance()


def _detail(key: str) -> None:
    publish("detail", mono.details(key, config.load().get("daily_budget", 280)))


def _lamp() -> None:
    with router.lock:
        state = light.state()
    publish("lamp", state)


def publish_prices() -> None:
    data = prices.panel_data()
    if data:
        publish("prices", data)


def _prices(force: bool = False) -> None:
    if force or prices.stale():
        prices.refresh(telegram.Ctx(*_chat()))
    publish_prices()


def publish_sleep() -> None:
    data = sleep.panel_data()
    if data:
        publish("sleep", data)


def _sleep(force: bool = False) -> None:
    if force or sleep.stale():
        sleep.refresh(telegram.Ctx(*_chat()))
    publish_sleep()


def _chat() -> tuple:
    cfg = config.load()
    return cfg["token"], cfg["chat_id"], cfg


def _station(force: bool = False) -> None:
    publish("station", station.panel_data(config.load(), force))


# panel-only requests that return data instead of running a chat command
REQUESTS = {
    "fin-panel": lambda arg: _finance(arg == "force"),
    "fin-detail": _detail,
    "lamp-state": lambda arg: _lamp(),
    "station-panel": lambda arg: _station(arg == "force"),
    "rem-list": lambda arg: publish("reminders", reminders.panel_list()),
    "prices-panel": lambda arg: _prices(arg == "force"),
    "sleep-panel": lambda arg: _sleep(arg == "force"),
}


def _command(text: str, msg_id: str) -> None:
    name, _, arg = text.partition(":")
    if name in REQUESTS:
        REQUESTS[name](arg)
        return
    ctx = telegram.Ctx(*_chat())
    # "re" ties the answer to the command, so a slow answer does not land on another screen
    reply = lambda text, buttons: publish("reply", {"text": text, "buttons": buttons, "re": msg_id})  # noqa: E731
    with router.lock, telegram.redirected(reply):
        router.handle(ctx, text)


def _menu_button() -> None:
    # the button left of the message field: always there, whatever keyboard a section shows
    cfg, url = config.load(), keyboards.panel_url()
    if not url or not cfg.get("chat_id"):
        return
    button = {"type": "web_app", "text": "Панель", "web_app": {"url": url}}
    try:
        telegram.api(cfg["token"], "setChatMenuButton", chat_id=cfg["chat_id"], menu_button=button)
    except Exception as e:
        log(f"панель, кнопка меню: {e!r}")


def start() -> None:
    if not config.load().get("panel_topic"):
        return
    threading.Thread(target=_menu_button, daemon=True).start()
    money.on_synced.append(publish_finance)
    prices.on_checked.append(publish_prices)
    sleep.on_synced.append(publish_sleep)
    relay.start(_command)
