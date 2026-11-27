from app import config

ECO, NORMAL, STATUS = "🔋 Еко", "⚡ Звичайний", "📊 Статус"
MIN = "🌑 Мінімум"
PC, BACK = "💻 ПК", "⬅️ Назад"
LIGHT = "💡 Світло"
MONEY, SYNC = "💰 Гроші", "🔄 Оновити виписку"
REM, REM_LIST, REM_NEW, PILLS = "⏰ Нагадування", "📋 Список", "➕ Нове", "💊 Таблетки"
STATION = "🔌 Станція"

LIGHT_PRESETS = [
    ["💡 Увімкнути", "🌑 Вимкнути", "🌙 Нічник"],
    ["🔅 25%", "🔆 60%", "☀️ 100%"],
    ["🕯 Тепле", "🌤 Денне", "❄️ Холодне"],
    ["🔴", "🟢", "🔵", "🟣"],
]


def kb(rows: list) -> dict:
    return {
        "keyboard": [[t if isinstance(t, dict) else {"text": t} for t in row] for row in rows],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def panel_url() -> str | None:
    cfg = config.load()
    if not cfg.get("webapp_url"):
        return None
    query = f"t={cfg['panel_topic']}&k={cfg['panel_key']}&v={cfg.get('panel_version', 1)}"
    return f"{cfg['webapp_url']}?{query}"


# the chat keeps only the lamp, everything else lives in the panel (menu button left of the message field)
MAIN = kb([["💡 Увімкнути", "🌑 Вимкнути", "🌙 Нічник"]])
DEFAULT = MAIN

BUTTONS = {
    ECO,
    NORMAL,
    MIN,
    STATUS,
    PC,
    BACK,
    LIGHT,
    MONEY,
    SYNC,
    REM,
    REM_LIST,
    REM_NEW,
    PILLS,
    STATION,
    *(b for row in LIGHT_PRESETS for b in row),
}
