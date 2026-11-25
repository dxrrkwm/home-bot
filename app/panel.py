# telegram mini app (github.com/dxrrkwm/home-bot-panel) talks to the bot through ntfy:
# commands come to <topic>, encrypted answers go to <topic>d, the key lives only in the panel button url
import base64
import hmac
import json
import os
import threading
import time
import urllib.request
import zlib

from app import config, keyboards, router, telegram
from app.features import light, money, station
from app.log import log
from app.services import mono

NTFY = "https://ntfy.sh"
HEADERS = {"User-Agent": "home-bot"}


def seal(obj, key: str) -> str:
    raw_key = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
    data = zlib.compress(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode(), 9)
    nonce = os.urandom(16)
    stream = b"".join(
        hmac.digest(raw_key, b"e" + nonce + i.to_bytes(4, "big"), "sha256") for i in range(len(data) // 32 + 1)
    )
    body = bytes(a ^ b for a, b in zip(data, stream))
    tag = hmac.digest(raw_key, b"m" + nonce + body, "sha256")[:16]
    return base64.b64encode(nonce + body + tag).decode()


def publish(kind: str, obj) -> None:
    cfg = config.load()
    req = urllib.request.Request(
        f"{NTFY}/{cfg['panel_topic']}d", data=seal(obj, cfg["panel_key"]).encode(), headers={**HEADERS, "Title": kind}
    )
    urllib.request.urlopen(req, timeout=20).close()


def publish_finance() -> None:
    data = mono.panel_data(config.load().get("daily_budget", 280))
    if data:
        data["syncing"] = mono.is_syncing()
        publish("fin", data)


FIN_STALE_SEC = 15 * 60
FIN_FORCE_SEC = 60  # monobank gives a statement once a minute


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


def _station(force: bool = False) -> None:
    publish("station", station.panel_data(config.load(), force))


# panel-only requests that return data instead of running a chat command
REQUESTS = {
    "fin-panel": lambda arg: _finance(arg == "force"),
    "fin-detail": _detail,
    "lamp-state": lambda arg: _lamp(),
    "station-panel": lambda arg: _station(arg == "force"),
}


def _command(text: str, msg_id: str = "") -> None:
    name, _, arg = text.partition(":")
    if name in REQUESTS:
        REQUESTS[name](arg)
        return
    cfg = config.load()
    ctx = telegram.Ctx(cfg["token"], cfg["chat_id"], cfg)
    # "re" ties the answer to the command, so a slow answer does not land on another screen
    reply = lambda text, buttons: publish("reply", {"text": text, "buttons": buttons, "re": msg_id})  # noqa: E731
    with router.lock, telegram.redirected(reply):
        router.handle(ctx, text)


def _run(text: str, msg_id: str) -> None:
    try:
        _command(text, msg_id)
    except Exception as e:
        log(f"панель, команда: {e!r}")


def _loop(topic: str) -> None:
    while True:
        try:
            req = urllib.request.Request(f"{NTFY}/{topic}/json", headers=HEADERS)
            with urllib.request.urlopen(req, timeout=90) as r:
                for line in r:
                    ev = json.loads(line)
                    if ev.get("event") != "message" or time.time() - ev.get("time", 0) > 60:
                        continue
                    # own thread: a slow command must not hold the ones after it in this stream
                    threading.Thread(target=_run, args=(ev["message"], ev.get("id", "")), daemon=True).start()
        except Exception as e:
            if "timed out" not in str(e):
                log(f"панель: {e!r}")
                time.sleep(10)


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
    topic = config.load().get("panel_topic")
    if not topic:
        return
    threading.Thread(target=_menu_button, daemon=True).start()
    money.on_synced.append(publish_finance)
    threading.Thread(target=_loop, args=(topic,), daemon=True, name="panel").start()
