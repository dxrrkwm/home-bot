import re
import threading

from app import config
from app import keyboards as kb
from app.features import (
    band,
    digest,
    health,
    laptop,
    light,
    menu,
    money,
    outage,
    pills,
    power,
    presence,
    prices,
    reminders,
    sleep,
    station,
    system,
)
from app.log import log
from app.telegram import Ctx, api

# commands from the chat and from the panel run one at a time
lock = threading.Lock()

# order matters: the first feature that understands the text answers, menu catches the rest
FEATURES = [
    system,
    health,
    pills,
    presence,
    station,
    outage,
    reminders,
    light,
    laptop,
    money,
    prices,
    sleep,
    band,
    digest,
    menu,
]
# features with background work: start() once, tick(ctx) on every loop
BACKGROUND = [station, outage, power, reminders, pills, money, prices, sleep, band, system, laptop, presence, digest]

# short words people type, or words in other languages
ALIASES = {
    "світло": "toggle_light",
    "свет": "toggle_light",
    "лампа": "toggle_light",
    "нічник": "🌙 Нічник",
    "ночник": "🌙 Нічник",
    "еко": kb.ECO,
    "эко": kb.ECO,
    "звичайний": kb.NORMAL,
    "обычный": kb.NORMAL,
    "мінімум": kb.MIN,
    "минимум": kb.MIN,
    "йду": kb.MIN,
    "ухожу": kb.MIN,
    "статус": kb.STATUS,
    "нагадування": kb.REM_LIST,
    "напоминания": kb.REM_LIST,
    "таблетки": kb.PILLS,
    "ліки": kb.PILLS,
    "скасувати нагадування": "clear_rem",
    "отменить напоминания": "clear_rem",
    "гроші": kb.MONEY,
    "деньги": kb.MONEY,
    "бюджет": kb.MONEY,
    "графік": "sched",
    "график": "sched",
    "відключення": "sched",
    "отключения": "sched",
    "станція": kb.STATION,
    "ліхтар": "led:toggle",
    "фонарь": "led:toggle",
    "фонарик": "led:toggle",
    "станция": kb.STATION,
    "заряд": kb.STATION,
    "ціни": prices.SHOW,
    "цены": prices.SHOW,
    "перекус": prices.SHOW,
    "енергетики": prices.SHOW,
    "энергетики": prices.SHOW,
    "оновити ціни": prices.FORCE,
    "обновить цены": prices.FORCE,
    "сон": sleep.SHOW,
    "сплю": band.SLEEP,
    "спати": band.SLEEP,
    "спать": band.SLEEP,
    "лягаю": band.SLEEP,
    "ложусь": band.SLEEP,
    "добраніч": band.SLEEP,
    "спокойной ночи": band.SLEEP,
    "встав": band.WAKE,
    "встал": band.WAKE,
    "прокинувся": band.WAKE,
    "проснулся": band.WAKE,
    "доброго ранку": band.WAKE,
    "доброе утро": band.WAKE,
    "оновити сон": sleep.FORCE,
    "обновить сон": sleep.FORCE,
    "назад": kb.BACK,
    "меню": kb.BACK,
    "пк": kb.PC,
    "ноут": kb.PC,
}


def _alias(text: str) -> str:
    if text in kb.BUTTONS:
        return text
    return ALIASES.get(re.sub(r"[^\w\s]", "", text.lower()).strip(), text)


def handle(ctx: Ctx, text: str) -> None:
    text = _alias(text)
    for feature in FEATURES:
        if feature.handle(ctx, text):
            return


def handle_update(token: str, cfg: dict, upd: dict) -> None:
    doc = None
    if "callback_query" in upd:
        cq = upd["callback_query"]
        chat = cq["message"]["chat"]["id"]
        api(token, "answerCallbackQuery", callback_query_id=cq["id"])
        text = cq.get("data")
        msg_id = None
    elif "message" in upd:
        chat = upd["message"]["chat"]["id"]
        text = upd["message"].get("text", "")
        doc = upd["message"].get("document")  # e.g. the shift schedule as csv
        msg_id = upd["message"]["message_id"]
        if "web_app_data" in upd["message"]:  # old panel buttons send commands this way
            text = upd["message"]["web_app_data"]["data"]
    else:
        return

    ctx = Ctx(token, chat, cfg, msg_id)
    if not cfg.get("chat_id"):
        if text == "/start":
            cfg["chat_id"] = chat
            config.save(cfg)
            log(f"привязан chat_id {chat}")
            ctx.send(
                "Готово, бот прив'язаний до тебе. Інші люди не зможуть ним керувати.\n\nОбери розділ 👇", menu=kb.MAIN
            )
        return
    if chat != cfg["chat_id"]:
        log(f"чужий чат {chat} проігноровано")
        return
    with lock:
        if doc:
            band.document(ctx, doc)
        else:
            handle(ctx, text)


def start_background() -> None:
    for feature in BACKGROUND:
        if hasattr(feature, "start"):
            feature.start()


def tick(ctx: Ctx) -> None:
    for feature in BACKGROUND:
        try:
            feature.tick(ctx)
        except Exception as e:  # one broken feature should not stop the others
            log(f"{feature.__name__}: {e!r}")
