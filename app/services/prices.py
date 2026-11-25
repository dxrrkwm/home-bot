# energy drinks and snack bars at silpo and novus (zakaz.ua): both have open json apis, no login
# drinks are compared per can, bars per 100 g, a deal is anything at or below the group target
import json
import re
import threading
import time
import urllib.parse
import urllib.request

from app.paths import ROOT

SETTINGS = ROOT / "prices.json"
EXAMPLE = ROOT / "prices.example.json"
CACHE = ROOT / "prices_cache.json"
STATE = ROOT / "prices_state.json"

SILPO = "https://sf-ecom-api.silpo.ua/v1/uk/branches/00000000-0000-0000-0000-000000000000/products"
SILPO_IMG = "https://images.silpo.ua/products/300x300/"
NOVUS = "https://stores-api.zakaz.ua/stores/{store}/categories/{cat}/products/"
BAR_G = 45
HEADERS = {"User-Agent": "Mozilla/5.0 home-bot", "Accept-Language": "uk"}

_busy = threading.Lock()


def settings() -> dict:
    path = SETTINGS if SETTINGS.exists() else EXAMPLE
    return json.loads(path.read_text(encoding="utf-8"))


def set_target(group: str, value: float) -> None:
    cfg = settings()
    cfg["groups"][group]["target"] = value
    SETTINGS.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _get(url: str, **params) -> dict:
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _brand(title: str, brands: list) -> str | None:
    # whole words, spaces optional: "Non Stop" finds "NonStop", "Nuts" does not find "Peanuts"
    title = title.lower()
    words = lambda b: r"\b" + r"\s*".join(map(re.escape, b.lower().split())) + r"\b"
    return next((b for b in brands if re.search(words(b), title)), None)


def _pieces(text: str) -> int:
    if m := re.search(r"(\d)\s*\+\s*(\d)", text):  # "2+1"
        return int(m[1]) + int(m[2])
    if m := re.search(r"(\d+)\s*[*xх×]\s*\d", text, re.I):  # "4*50г", "2х30г"
        return int(m[1])
    return 1


def _ml(text: str) -> float | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(мл|л)\b", text)
    if not m:
        return None
    n = float(m[1].replace(",", "."))
    return n if m[2] == "мл" else n * 1000


def _grams(text: str) -> float | None:
    m = re.search(r"(?:(\d+)\s*[*xх×]\s*)?(\d+(?:[.,]\d+)?)\s*г\b", text)
    return float(m[2].replace(",", ".")) * int(m[1] or 1) if m else None


def _silpo(cat: str) -> list:
    items, offset = [], 0
    while True:
        page = _get(SILPO, limit=100, offset=offset, category=cat)
        for p in page["items"]:
            if p.get("weighted") or not p.get("stock"):
                continue
            bulk = next((s for s in p.get("specialPrices") or [] if s.get("type") == "from"), None)
            items.append(
                {
                    "shop": "Сільпо",
                    "id": f"s{p['externalProductId']}",
                    "t": p["title"],
                    "size": p.get("displayRatio") or "",
                    "p": p["price"],
                    "old": p.get("oldPrice"),
                    "bulk": [bulk["count"], bulk["price"]] if bulk else None,
                    "img": SILPO_IMG + p["icon"] if p.get("icon") else None,
                    "url": f"https://silpo.ua/product/{p['slug']}",
                }
            )
        offset += 100
        if offset >= page["total"]:
            return items


def _novus(store: int, cat: str) -> list:
    items, page = [], 1
    while True:
        data = _get(NOVUS.format(store=store, cat=cat), per_page=100, page=page)
        for p in data["results"]:
            if not p.get("in_stock") or p.get("unit") != "pcs":
                continue
            size = ""
            if p.get("weight"):
                size = f"{p['weight']:g}г"
            elif p.get("volume"):
                size = f"{p['volume'] / 1000:g}л".replace(".", ",")
            items.append(
                {
                    "shop": "Novus",
                    "id": f"n{p['ean']}",
                    "t": p["title"],
                    "size": size,
                    "p": p["price"] / 100,
                    "old": p["discount"]["old_price"] / 100 if p["discount"]["status"] else None,
                    "bulk": None,
                    "img": (p.get("img") or {}).get("s350x350"),
                    "url": p["web_url"].replace("/en/", "/uk/"),
                }
            )
        if page * 100 >= data["count"]:
            return items
        page += 1


def _fit(item: dict, group: dict, pack: int) -> dict | None:
    # price for one piece (with a "from N pieces" offer if a pack covers it) and the value compared to the target
    text = f"{item['t']} {item['size']}"
    item["brand"] = _brand(item["t"], group["brands"])
    if not item["brand"]:
        return None
    price = item["p"]
    if item["bulk"] and item["bulk"][0] <= pack:
        price = min(price, item["bulk"][1])
    if group.get("ml"):
        if _ml(item["size"]) != group["ml"] or _pieces(text) != 1:
            return None
        item["one"] = item["v"] = price
    else:
        grams = _grams(item["size"]) or _grams(item["t"])
        if not grams:
            return None
        # multipacks often do not say how many bars are inside: count them by a usual bar size
        pieces = _pieces(text)
        if pieces == 1:
            pieces = max(1, round(grams / BAR_G))
        item["one"] = price / pieces
        item["v"] = price / grams * 100
        item["pieces"] = pieces
    item["deal"] = item["v"] <= group["target"]
    return item


def check() -> dict:
    cfg = settings()
    groups, errors = {}, []
    for key, group in cfg["groups"].items():
        found = []
        for name, load in (
            ("Сільпо", lambda: _silpo(group["silpo"])),
            ("Novus", lambda: _novus(cfg["novus_store"], group["novus"])),
        ):
            try:
                found += load()
            except Exception as e:
                errors.append(f"{name}: {e!r}")
        found = [i for i in (_fit(i, group, cfg["pack"]) for i in found) if i]
        groups[key] = sorted(found, key=lambda i: i["v"])
    data = {"ts": time.time(), "groups": groups, "errors": errors}
    if any(groups.values()):
        CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def cached() -> dict | None:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return None


def age() -> float:
    return time.time() - CACHE.stat().st_mtime if CACHE.exists() else 1e9


def new_deals(data: dict) -> dict:
    # deals not announced yet, or cheaper than when announced; a deal that ended can be announced again later
    try:
        seen = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        seen = {}
    fresh, now = {}, {}
    for key, items in data["groups"].items():
        for i in items:
            if not i["deal"]:
                continue
            now[i["id"]] = i["v"]
            if i["id"] not in seen or i["v"] < seen[i["id"]] - 0.01:
                fresh.setdefault(key, []).append(i)
    STATE.write_text(json.dumps(now), encoding="utf-8")
    return fresh


def is_checking() -> bool:
    return _busy.locked()


def check_in_background(done) -> bool:
    if not _busy.acquire(blocking=False):
        return False

    def run():
        try:
            data, err = check(), None
        except Exception as e:
            data, err = None, e
        finally:
            _busy.release()
        done(data, err)

    threading.Thread(target=run, daemon=True, name="prices").start()
    return True
