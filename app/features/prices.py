import datetime as dt
import re
import time

from app.log import log
from app.services import prices
from app.telegram import Ctx

SHOW, FORCE = "prices", "prices:force"
CHECK_SEC = 4 * 3600
RETRY_SEC = 30 * 60
DAYTIME = range(9, 22)  # deals are announced only in the daytime
ICONS = {"drinks": "⚡", "bars": "🍫"}
GROUPS = {"енергетик": "drinks", "энергетик": "drinks", "батончик": "bars"}
TARGET_RE = re.compile(r"^(?:ціль|цель)\s+(енергетик|энергетик|батончик)\w*\s+(\d+(?:[.,]\d+)?)$", re.I)
# words that say nothing in a short product name
NOISE = re.compile(
    r"напій енергетичний|енергетичний напій|напій енерг|шоколадний батончик|батончик|nestl[eé]|®|"
    r"безалкогольн\w*|сильногазован\w*|газован\w*|з/б|\b0,5л\b|\b\d+(?:[.,]\d+)?г\b",
    re.I,
)
HELP = "Ціль: «ціль енергетик 33» (₴ за банку 0,5) або «ціль батончик 55» (₴ за 100 г)."

# called after every check (the panel keeps its card fresh this way)
on_checked = []
_last_try = 0.0


def money(v: float) -> str:
    return f"{v:.2f}".replace(".", ",")


def short(title: str) -> str:
    title = re.sub(r"\s{2,}", " ", NOISE.sub("", title)).strip(" ,.-")
    return title if len(title) <= 40 else title[:38].rstrip() + "…"


def _target(group: dict) -> str:
    return f"≤ {group['target']:g} ₴" + ("" if group.get("ml") else "/100г")


def _line(item: dict, group: dict, pack: int) -> str:
    old = f" (було {money(item['old'])}, −{100 - 100 * item['p'] / item['old']:.0f}%)" if item.get("old") else ""
    bulk = f", від {item['bulk'][0]} шт — {money(item['bulk'][1])}" if item.get("bulk") else ""
    per = "" if group.get("ml") else f" · {item['v']:.0f} ₴/100г"
    return (
        f"• {short(item['t'])} {item['size']} — {money(item['p'])} ₴{old}{bulk}{per} · {item['shop']}\n"
        f"   {pack} шт ≈ {item['one'] * pack:.0f} ₴"
    )


def _block(key: str, items: list, cfg: dict) -> str:
    group = cfg["groups"][key]
    lines = "\n".join(_line(i, group, cfg["pack"]) for i in items)
    return f"{ICONS.get(key, '•')} {group['name']} (ціль {_target(group)})\n{lines}"


def stale() -> bool:
    return prices.age() > CHECK_SEC


def summary() -> str:
    data, cfg = prices.cached(), prices.settings()
    if not data:
        return "🛒 Ще не перевіряв ціни — зараз гляну (≈ 10 с)."
    upd = dt.datetime.fromtimestamp(data["ts"]).strftime("%d.%m %H:%M")
    blocks = []
    for key, items in data["groups"].items():
        if key in cfg["groups"]:
            deals = [i for i in items if i["deal"]]
            top = deals[:5] or items[:3]
            blocks.append(_block(key, top, cfg) if top else f"{cfg['groups'][key]['name']}: нічого не знайшов")
    return f"🛒 Найдешевше зараз (Сільпо, Novus · {upd})\n\n" + "\n\n".join(blocks) + "\n\n" + HELP


def _announce(ctx: Ctx, fresh: dict) -> None:
    cfg = prices.settings()
    blocks = [_block(key, items[:5], cfg) for key, items in fresh.items()]
    best = [i for items in fresh.values() for i in items[:3]]
    buttons = [[{"text": f"🛒 {short(i['t'])[:28]} · {money(i['p'])}", "url": i["url"]}] for i in best[:6]]
    text = "🔥 Вигідна ціна — можна брати пачкою\n\n" + "\n\n".join(blocks)
    ctx.send(text, inline=buttons, preview=next((i["img"] for i in best if i.get("img")), None))


def refresh(ctx: Ctx | None, answer: Ctx | None = None) -> bool:
    # ctx: where to announce new deals; answer: someone asked in the chat and waits for the summary
    def done(data, err):
        if err:
            log(f"prices: {err!r}")
        elif data["errors"]:
            log(f"prices: {data['errors']}")
        for fn in on_checked:
            try:
                fn()
            except Exception as e:
                log(f"after prices: {e!r}")
        if answer:
            answer.send(summary() if data else f"🛒 Не вийшло перевірити ціни: {err}")
        if data and ctx:
            fresh = prices.new_deals(data)
            if fresh:
                _announce(ctx, fresh)

    return prices.check_in_background(done)


def handle(ctx: Ctx, text: str) -> bool:
    if m := TARGET_RE.match(text.strip()):
        key = GROUPS[m[1].lower()]
        prices.set_target(key, float(m[2].replace(",", ".")))
        group = prices.settings()["groups"][key]
        ctx.send(f"🎯 {group['name']}: ціль {_target(group)}. Перевіряю ціни…")
        refresh(ctx, ctx)
    elif text == SHOW:
        ctx.send(summary())
        if stale():
            refresh(ctx)
    elif text == FORCE:
        if not refresh(ctx, ctx):
            ctx.send("⏳ Вже перевіряю ціни, зачекай кілька секунд.")
    else:
        return False
    return True


def tick(ctx: Ctx) -> None:
    global _last_try
    if dt.datetime.now().hour not in DAYTIME or prices.is_checking():
        return
    if stale() and time.time() - _last_try > RETRY_SEC:
        _last_try = time.time()
        refresh(ctx)


def panel_data() -> dict | None:
    data, cfg = prices.cached(), prices.settings()
    if not data:
        return None
    keep = ("size", "shop", "p", "old", "bulk", "one", "v", "deal", "img", "url", "pieces")
    groups = [
        {
            "key": key,
            "name": cfg["groups"][key]["name"],
            "target": cfg["groups"][key]["target"],
            "per": "шт" if cfg["groups"][key].get("ml") else "100г",
            "items": [{"t": short(i["t"]), **{k: i.get(k) for k in keep}} for i in items[:15]],
        }
        for key, items in data["groups"].items()
        if key in cfg["groups"]
    ]
    upd = dt.datetime.fromtimestamp(data["ts"]).strftime("%d.%m %H:%M")
    return {"upd": upd, "pack": cfg["pack"], "checking": prices.is_checking(), "groups": groups}
