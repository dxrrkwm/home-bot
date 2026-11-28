# monobank personal api: balances and statement, 1 request a minute so sync runs in the background

import calendar
import csv
import datetime as dt
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from app.paths import ROOT


def _config():
    try:
        return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


# statement csv goes where other tools can read it, set "mono_dir" in config.json
OUT_DIR = Path(_config().get("mono_dir") or ROOT / "data" / "mono")
CSV_PATH = OUT_DIR / "statement.csv"
INFO_PATH = OUT_DIR / "client-info.json"  # no name or token, only accounts and jars
HISTORY_PATH = OUT_DIR / "history.json"  # a point a day of the debt and the jars
FIELDS = ["id", "time", "card", "account_type", "description", "mcc", "amount", "balance", "comment"]
API = "https://api.monobank.ua"
GAP = 62  # seconds between requests

_lock = threading.Lock()
_last_call = 0.0
_state = {"syncing": False, "last_sync": None, "error": None}


def token():
    try:
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return ""
    t = (cfg.get("mono_token") or "").strip()
    return "" if (not t or "ВСТАВ" in t) else t


def _get(path):
    global _last_call
    with _lock:
        wait = _last_call + GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(API + path, headers={"X-Token": token()})
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                # the hub and the laptop share the token, so one of them can hit the limit: wait and try once more
                if e.code != 429 or attempt:
                    raise
                time.sleep(GAP)
            finally:
                _last_call = time.time()


def _cards(info):
    # uah cards: black (credit limit) and white
    out = []
    for a in info.get("accounts", []):
        if a.get("currencyCode") != 980 or not a.get("maskedPan"):
            continue
        out.append(
            {
                "id": a["id"],
                "type": a.get("type", ""),
                "pan": a["maskedPan"][0],
                "balance": a["balance"] / 100,
                "limit": a.get("creditLimit", 0) / 100,
            }
        )
    return out


def _save_info(info):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    safe = {
        "saved": dt.datetime.now().isoformat(timespec="seconds"),
        "cards": _cards(info),
        "jars": [
            {"title": j.get("title"), "balance": j.get("balance", 0) / 100, "goal": (j.get("goal") or 0) / 100}
            for j in info.get("jars", [])
            if j.get("currencyCode") == 980
        ],
    }
    INFO_PATH.write_text(json.dumps(safe, ensure_ascii=False, indent=1), encoding="utf-8")
    _remember(safe)
    return safe


def _remember(safe):
    # one point a day of the black card debt and the jars, to see how fast the goals move
    try:
        hist = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        hist = {}
    debt = sum(max(c["limit"] - c["balance"], 0) for c in safe["cards"] if c["limit"] > 0)
    white = next((c["balance"] for c in safe["cards"] if c.get("type") == "white"), None)
    day = hist.setdefault(dt.date.today().isoformat(), {})
    day.update(debt=round(debt), jars={j["title"]: j["balance"] for j in safe["jars"]})
    if white is not None:
        day.setdefault("white_first", white)  # the first look of the day, the plan of a period starts here
        day["white"] = white
    keep = sorted(hist)[-400:]
    HISTORY_PATH.write_text(json.dumps({k: hist[k] for k in keep}, ensure_ascii=False), encoding="utf-8")


def _debt_change(days=30):
    # how much the debt went down over the last days, None while there is no history that old
    try:
        hist = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    old = [k for k in sorted(hist) if k <= since] or sorted(hist)
    if not old or old[-1] == dt.date.today().isoformat():
        return None
    return hist[old[-1]]["debt"] - hist[max(hist)]["debt"], (dt.date.today() - dt.date.fromisoformat(old[-1])).days


def _jar_flow(rows, title, days=30):
    # put into a jar minus taken out of it over the last days, from the card statement
    since = dt.date.today() - dt.timedelta(days=days)
    return -sum(
        float(r["amount"])
        for r in rows
        if dt.date.fromisoformat(r["time"][:10]) > since and title.lower() in r["description"].lower()
    )


MONTHS_GEN = ["січня", "лютого", "березня", "квітня", "травня", "червня", "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"]  # fmt: skip


def _date(d):
    return f"{d.day} {MONTHS_GEN[d.month - 1]}" + (f" {d.year}" if d.year != dt.date.today().year else "")


def _next_day(day, today):
    # the next date with this day of month, today included
    d = today.replace(day=min(day, 28))
    if d < today:
        d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=min(day, 28))
    return d


def allowance(payday, bills, goals_list):
    # how much can go out a day till the salary: the white card at the start of today minus the bills
    # due before payday and the part of the goals with a deadline, split over the days left
    if not INFO_PATH.exists():
        return None
    info = json.loads(INFO_PATH.read_text(encoding="utf-8"))
    white = next((c for c in info["cards"] if c.get("type") == "white"), None)
    if not white:
        return None
    today = dt.date.today()
    pay = _next_day(payday, today + dt.timedelta(days=1))
    days = (pay - today).days
    spent = -sum(
        float(r["amount"]) for r in _read_csv().values() if _is_daily_spend(r) and r["time"][:10] == today.isoformat()
    )
    reserve = []
    for b in bills:
        due = _next_day(b["day"], today)
        if due < pay:
            reserve.append({"n": b["name"], "sum": b["sum"], "when": _date(due)})
    start = white["balance"] + spent
    free = start - sum(r["sum"] for r in reserve)
    # the goals are put aside only when they fit, the bills come first
    skipped = []
    for g in goals_list:
        if g.get("need"):
            part = {"n": f"відкласти в «{g['n']}»", "sum": round(g["need"] * days / 30), "when": "до ЗП", "goal": True}
            if part["sum"] <= free:
                reserve.append(part)
                free -= part["sum"]
            else:
                skipped.append(part)
    # nothing left after today's spending: no money a day, whatever the morning said
    per_day = max(free, 0) / days if free > spent else 0
    return {
        "per_day": round(per_day),
        "spent": round(spent),
        "left": round(per_day - spent),
        "days": days,
        "payday": _date(pay),
        "balance": round(white["balance"]),
        "reserve": reserve,
        "skipped": skipped,
        "free": round(free),  # at the start of today
        "free_now": round(free - spent),
    }


def _n(n):
    return f"{n:,.0f}".replace(",", " ")


def _history():
    try:
        return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def till_payday(a, bills, avg):
    # a column a day from a week ago to the salary: what went out, what is allowed, the bills on their days,
    # and where the current pace ends up on payday
    today = dt.date.today()
    pay = today + dt.timedelta(days=a["days"])
    first = max(today - dt.timedelta(days=6), (pay.replace(day=1) - dt.timedelta(days=1)).replace(day=pay.day))
    spent = {}
    for r in _read_csv().values():
        if _is_daily_spend(r) and r["time"][:10] >= first.isoformat():
            spent[r["time"][:10]] = spent.get(r["time"][:10], 0) - float(r["amount"])
    due = {_next_day(b["day"], today).isoformat(): b for b in bills}
    days = []
    d = first
    while d <= pay:
        k = d.isoformat()
        day = {
            "d": d.strftime("%d.%m"),
            "w": d.weekday(),
            "when": "past" if d < today else "today" if d == today else "next",
        }
        if d <= today:
            day["spent"] = round(spent.get(k, 0))
        if k in due and today <= d < pay:
            day["bill"] = [due[k]["name"], due[k]["sum"]]
        if d == pay:
            day["when"] = "pay"
        if today <= d < pay:
            # free money left at the end of the day if today and every next day go like the last week
            day["left"] = round(a["free"] - max(a["spent"], avg) - avg * (d - today).days)
        days.append(day)
        d += dt.timedelta(days=1)
    end = a["free"] - max(a["spent"], avg) - avg * (a["days"] - 1)
    return {"days": days, "per_day": a["per_day"], "avg": round(avg), "end": round(end), "payday": a["payday"]}


def forecast_text(t):
    pace = f"Якщо витрачати як цього тижня (≈ {_n(t['avg'])}/день)"
    if t["end"] >= 0:
        return f"📈 {pace}, на {t['payday']} лишиться ≈ {_n(t['end'])} грн."
    if not t["per_day"]:
        return f"📉 {pace}, до {t['payday']} буде мінус ≈ {_n(-t['end'])} грн."
    return f"📉 {pace}, до {t['payday']} не вистачить ≈ {_n(-t['end'])} грн. Тримай ≈ {_n(t['per_day'])}/день — і зійдеться."


def allowance_text(a):
    head = f"💸 До зарплати ({a['payday']}) {a['days']} дн."
    owed = ", ".join(f"{r['n']} {_n(r['sum'])} ({r['when']})" for r in a["reserve"])
    if not a["per_day"]:
        short = f"бракує {_n(-a['free_now'])} грн" if a["free_now"] < 0 else "впритул"
        lines = [f"{head} Вільних грошей нема: на білій {_n(a['balance'])}, а до ЗП ще {owed or 'нічого'} — {short}."]
        if a["spent"]:
            lines.append(f"Сьогодні витрачено {_n(a['spent'])} грн.")
    else:
        left = a["left"]
        lines = [
            f"{head} Можна ≈ {_n(a['per_day'])} грн на день.",
            f"Сьогодні: {_n(a['spent'])} грн, " + (f"ще можна {_n(left)}" if left >= 0 else f"понад на {_n(-left)}"),
        ]
        if owed:
            lines.append("Відкладено з білої: " + owed.replace("), ", "); "))
    for g in a.get("skipped", []):
        lines.append(f"🎯 {g['n'][0].upper() + g['n'][1:]} {_n(g['sum'])} до ЗП не вистачає — відклади з зарплати.")
    return "\n".join(lines)


def saved_goals(deadlines=None):
    if not INFO_PATH.exists():
        return []
    info = json.loads(INFO_PATH.read_text(encoding="utf-8"))
    return goals(info, list(_read_csv().values()), deadlines)


def goals_text(items):
    lines = []
    for g in items:
        if g["kind"] == "debt":
            line = f"⚫ Борг по чорній: {g['bal']:,} з {g['goal']:,}".replace(",", " ")
            if g.get("month"):
                line += f" · {'−' if g['month'] > 0 else '+'}{abs(g['month']):,}/міс".replace(",", " ")
        else:
            line = f"🎯 {g['n']}: {g['bal']:,} з {g['goal']:,} ({100 * g['bal'] // g['goal']}%)".replace(",", " ")
            if g["month"]:
                line += f" · +{g['month']:,} за 30 дн".replace(",", " ")
            if g.get("deadline"):
                line += f"\n   до {g['deadline']} треба ≈ {g['need']:,}/міс".replace(",", " ")
        if g.get("eta"):
            line += f" → у такому темпі {g['eta']}"
        lines.append(line)
    return "\n".join(lines)


def goals(info, rows, deadlines=None):
    # jars with a goal and the black card debt: where it is, how fast it moves, when it gets there
    deadlines = deadlines or {}
    today = dt.date.today()
    out = []
    for j in info.get("jars", []):
        if not j.get("goal"):
            continue
        left = max(j["goal"] - j["balance"], 0)
        month = _jar_flow(rows, j["title"])
        g = {
            "n": j["title"],
            "kind": "jar",
            "bal": round(j["balance"]),
            "goal": round(j["goal"]),
            "month": round(month),
        }
        if left and month > 0:
            g["eta"] = _date(today + dt.timedelta(days=int(left / (month / 30))))
        if when := deadlines.get(j["title"]):
            end = dt.date.fromisoformat(when)
            months = max((end - today).days / 30, 0.5)
            g["deadline"] = _date(end)
            g["need"] = round(left / months)
            g["late"] = month < g["need"]
        out.append(g)
    for c in info["cards"]:
        if c["limit"] > 0:
            debt = max(c["limit"] - c["balance"], 0)
            g = {"n": "Чорна картка", "kind": "debt", "bal": round(debt), "goal": round(c["limit"])}
            if (change := _debt_change()) is not None:
                paid, days = change
                g["month"] = round(paid / days * 30)
                if debt and paid > 0:
                    g["eta"] = _date(today + dt.timedelta(days=int(debt / (paid / days))))
            out.append(g)
    return out


def _read_csv():
    if not CSV_PATH.exists():
        return {}
    with CSV_PATH.open(encoding="utf-8-sig", newline="") as f:
        return {r["id"]: r for r in csv.DictReader(f)}


def _write_csv(rows):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CSV_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in sorted(rows.values(), key=lambda r: r["time"]):
            w.writerow(r)
    tmp.replace(CSV_PATH)


def sync(days=31):
    if not token():
        raise RuntimeError("немає mono_token у config.json")
    try:
        before = {c["id"]: c["balance"] for c in json.loads(INFO_PATH.read_text(encoding="utf-8"))["cards"]}
    except Exception:
        before = {}
    info = _get("/personal/client-info")
    safe = _save_info(info)
    rows = _read_csv()
    to = int(time.time())
    frm = to - min(days, 31) * 86400
    for c in safe["cards"]:
        # a minute a request: a card whose balance did not move is asked next time it does
        if rows and before.get(c["id"]) == c["balance"]:
            continue
        items = _get(f"/personal/statement/{c['id']}/{frm}/{to}")
        kind = "чорна" if c["limit"] > 0 else "біла"
        for s in items:
            rows[s["id"]] = {
                "id": s["id"],
                "time": dt.datetime.fromtimestamp(s["time"]).isoformat(timespec="seconds"),
                "card": c["pan"][-4:],
                "account_type": kind,
                "description": s.get("description", ""),
                "mcc": s.get("mcc", ""),
                "amount": s["amount"] / 100,
                "balance": s.get("balance", 0) / 100,
                "comment": s.get("comment", ""),
            }
    _write_csv(rows)
    return safe


def sync_in_background(days=31, done=None):
    if _state["syncing"]:
        return False

    def run():
        _state["syncing"] = True
        err = None
        try:
            sync(days)
            _state["last_sync"] = time.time()
        except Exception as e:  # network, token, rate limit
            err = e
        _state["syncing"], _state["error"] = False, err
        if done:
            done(err)

    threading.Thread(target=run, daemon=True).start()
    return True


def is_syncing():
    return _state["syncing"]


# ---------- summary for the bot ----------
SKIP_WORDS = (
    "На уник",
    "Оплата Оренди",
    "Оплата Комуналки",
    "Комуналка",
    "Між своїми",
    "З білої картки",
    "З чорної картки",
    "в рамках міграції рахунку",  # the bank moving the black card to a new account: in and out the same minute
)
TRANSFERS = {"4829", "6012"}  # money transfers, other banks


def _is_daily_spend(r):
    # daily spending: card purchases (refunds count back) except transfers to people, jars and own cards
    if str(r.get("mcc")) in TRANSFERS:
        return False
    d = r["description"]
    return not any(w.lower() in d.lower() for w in SKIP_WORDS)


def _who(desc):
    # "Від: Анастасія Новікова" and "Анастасія Н." are the same person: the first name and the surname's letter
    words = desc.removeprefix("Від:").replace(",", " ").split()
    return (words[0].lower(), words[1][0].lower()) if len(words) > 1 else None


def _lent_back(r, rows):
    # money sent to someone that came back from them in two weeks is a returned loan, not income
    who, when = _who(r["description"]), dt.datetime.fromisoformat(r["time"])
    return who is not None and any(
        float(o["amount"]) == -float(r["amount"])
        and _who(o["description"]) == who
        and dt.timedelta(0) < when - dt.datetime.fromisoformat(o["time"]) < dt.timedelta(days=14)
        for o in rows
    )


MARKS_PATH = ROOT / "money_state.json"  # {"transit": {operation id: how much of it is someone else's}}


def _transit():
    try:
        return json.loads(MARKS_PATH.read_text(encoding="utf-8")).get("transit", {})
    except Exception:
        return {}


def _own(r):
    # the part of an operation that is really mine, without someone else's money passing through
    return float(r["amount"]) - _transit().get(r["id"], 0)


def _is_income(r, rows):
    # money from outside: salary, the fop account, cash in, people; not own cards, jars, refunds, a loan back or transit
    if float(r["amount"]) <= 0 or str(r.get("mcc")) not in TRANSFERS or _own(r) <= 0:
        return False
    d = r["description"]
    if "«" in d or any(w.lower() in d.lower() for w in SKIP_WORDS):  # «» is a jar
        return False
    return not _lent_back(r, rows)


def summary(daily_budget=280, today_line=True):
    if not INFO_PATH.exists():
        return None
    info = json.loads(INFO_PATH.read_text(encoding="utf-8"))
    rows = list(_read_csv().values())
    today = dt.date.today()
    spent = lambda d0, d1: -sum(
        float(r["amount"]) for r in rows if _is_daily_spend(r) and d0 <= dt.date.fromisoformat(r["time"][:10]) <= d1
    )
    t = spent(today, today)
    w = spent(today - dt.timedelta(days=6), today) / 7
    lines = []
    fmt = lambda n: f"{n:,.0f}".replace(",", " ")
    for c in info["cards"]:
        if c["limit"] > 0:
            used = c["limit"] - c["balance"] if c["balance"] < c["limit"] else 0
            lines.append(f"⚫ Чорна: борг по ліміту {fmt(used)} з {fmt(c['limit'])}")
        elif c.get("type") == "white":
            lines.append(f"⚪ Біла: {fmt(c['balance'])} грн")
        elif c["balance"]:
            lines.append(f"💳 {c.get('type')} …{c['pan'][-4:]}: {fmt(c['balance'])} грн")
    for j in info.get("jars", []):
        lines.append(f"🫙 {j['title']}: {fmt(j['balance'])} грн" + (f" з {fmt(j['goal'])}" if j.get("goal") else ""))
    left = daily_budget - t
    lines.append("")
    if today_line:
        lines.append(
            f"Сьогодні витрачено: {t:,.0f} з {daily_budget} грн".replace(",", " ")
            + (
                f" (ще можна {left:,.0f})".replace(",", " ")
                if left >= 0
                else f" (понад бюджет на {-left:,.0f})".replace(",", " ")
            )
        )
    lines.append(f"Середньо за 7 днів: {w:,.0f} грн/день".replace(",", " "))
    saved = dt.datetime.fromisoformat(info["saved"])
    lines.append(f"\nОновлено: {saved:%d.%m %H:%M}")
    return "\n".join(lines)


# ---------- data for the panel ----------
CATS = [
    ("Продукти", {5411, 5412, 5422, 5441, 5451, 5462, 5499, 5300}),
    ("Кафе й фастфуд", {5812, 5813, 5814}),
    ("Транспорт", {4111, 4121, 4131, 4784, 4789, 5541, 5542, 7523}),
    ("Ігри й підписки", {5815, 5816, 5817, 5818, 7994, 4899, 7372}),
    ("Здоров'я", {5912, 8011, 8021, 8043, 8062, 8099}),
    ("Покупки", {5045, 5311, 5331, 5399, 5651, 5691, 5732, 5734, 5945, 5964, 5999, 5200, 5251}),
]


def _cat(mcc):
    mcc = int(mcc or 0)
    return next((name for name, codes in CATS if mcc in codes), "Інше")


def panel_data(daily_budget=280):
    if not INFO_PATH.exists():
        return None
    info = json.loads(INFO_PATH.read_text(encoding="utf-8"))
    rows = sorted(_read_csv().values(), key=lambda r: r["time"])
    today = dt.date.today()
    day = lambda r: dt.date.fromisoformat(r["time"][:10])
    spend = [r for r in rows if _is_daily_spend(r)]

    days = []
    for i in range(13, -1, -1):
        d = today - dt.timedelta(days=i)
        days.append([d.strftime("%d.%m"), round(-sum(float(r["amount"]) for r in spend if day(r) == d))])

    month = [r for r in rows if day(r).replace(day=1) == today.replace(day=1)]
    cats = {}
    for r in month:
        if _is_daily_spend(r):
            cats[_cat(r["mcc"])] = cats.get(_cat(r["mcc"]), 0) - float(r["amount"])

    cards = []
    for c in info["cards"]:
        if c["limit"] > 0:
            cards.append({"n": "Чорна", "debt": round(max(c["limit"] - c["balance"], 0)), "lim": round(c["limit"])})
        elif c.get("type") == "white" or c["balance"]:
            cards.append({"n": "Біла" if c.get("type") == "white" else c.get("type"), "bal": round(c["balance"])})
    jars = [
        {"n": j["title"], "bal": round(j["balance"]), "goal": round(j.get("goal") or 0)} for j in info.get("jars", [])
    ]

    return {
        "upd": dt.datetime.fromisoformat(info["saved"]).strftime("%d.%m %H:%M"),
        "budget": daily_budget,
        "cards": cards,
        "jars": jars,
        "days": days,
        "avg7": round(sum(v for _, v in days[-7:]) / 7),
        "mspent": round(-sum(float(r["amount"]) for r in month if _is_daily_spend(r))),
        "mincome": round(sum(_own(r) for r in month if _is_income(r, rows))),
        "cats": sorted([[k, round(v)] for k, v in cats.items()], key=lambda x: -x[1]),
        "tips": insights(rows, daily_budget),
        "goods": goods(rows, daily_budget),
        "tx": [
            [
                r["time"][8:10] + "." + r["time"][5:7] + " " + r["time"][11:16],
                r["description"][:32],
                round(float(r["amount"])),
            ]
            for r in rows[-12:][::-1]
        ],
    }


CAFE = {5812, 5813, 5814}
TRANSPORT = {4111, 4121, 4131, 4789}
HEALTH = {5912, 8011, 8021, 8043, 8062, 8099}
SHOPS = {5411, 5499, 5331, 5310}


def _has(r, *words):
    return any(w.lower() in r["description"].lower() for w in words)


def _filter(key, rows=()):
    # single place that says which operations belong to a panel item, used by totals and details
    kind, _, arg = key.partition(":")
    spend = _is_daily_spend
    mcc = lambda r: int(r["mcc"] or 0)
    return {
        "shop": lambda r: spend(r) and r["description"] == arg,
        "cafe": lambda r: spend(r) and mcc(r) in CAFE,
        "transport": lambda r: spend(r) and mcc(r) in TRANSPORT,
        "health": lambda r: spend(r) and mcc(r) in HEALTH,
        "big": lambda r: spend(r) and mcc(r) == 5411,
        "cat": lambda r: spend(r) and _cat(r["mcc"]) == arg,
        "saved": lambda r: float(r["amount"]) < 0 and _has(r, "На уник"),
        "bills": lambda r: float(r["amount"]) < 0 and _has(r, "Оплата Оренди", "Оплата Комуналки"),
        "over": spend,
        "income": lambda r: _is_income(r, rows),
    }[kind]


def _period(rows, key):
    today = dt.date.today()
    if key.startswith("cat:") or key == "income":
        return [r for r in rows if r["time"][:7] == today.strftime("%Y-%m")]
    return [r for r in rows if (today - dt.date.fromisoformat(r["time"][:10])).days < 30]


def _sum(rows, key):
    f = _filter(key)
    return -sum(float(r["amount"]) for r in _period(rows, key) if f(r))


def _spend_days(rows):
    days = {}
    for r in _period(rows, "over"):
        if _is_daily_spend(r):
            days[r["time"][:10]] = days.get(r["time"][:10], 0) - float(r["amount"])
    return days


def insights(rows, daily_budget):
    total = _sum(rows, "over") or 1
    count = {}
    for r in _period(rows, "over"):
        if _is_daily_spend(r) and float(r["amount"]) < 0 and int(r["mcc"] or 0) in SHOPS:
            count[r["description"]] = count.get(r["description"], 0) + 1
    out = []

    # frequent shopping in one small store, usually the one downstairs
    for name, n in sorted(count.items(), key=lambda x: -_sum(rows, "shop:" + x[0])):
        if n < 8:
            continue
        amount = _sum(rows, "shop:" + name)
        tip = (
            "Продукти на 3–4 дні в АТБ/Фора/Сільпо зазвичай на 20–30% дешевші, а тут бери лише дрібниці."
            if not out
            else "Перекуси й енергетики дешевше брати в супермаркеті упаковкою."
        )
        out.append(
            {
                "id": "shop:" + name,
                "t": f"🏪 {name}",
                "v": round(amount),
                "save": round(amount * 0.25),
                "d": f"За 30 днів: {n} разів, середній чек {amount / n:.0f} грн ({100 * amount / total:.0f}% усіх витрат). "
                + tip,
            }
        )

    cafe = _sum(rows, "cafe")
    if cafe > 600:
        out.append(
            {
                "id": "cafe",
                "t": "🍔 Кафе й фастфуд",
                "v": round(cafe),
                "save": round(cafe / 2 * 0.7),
                "d": "Половину таких походів можна замінити на запіканку в духовці — виходить у 3–4 рази дешевше.",
            }
        )

    over = [v - daily_budget for v in _spend_days(rows).values() if v > daily_budget]
    if over:
        out.append(
            {
                "id": "over",
                "t": "📈 Дні понад бюджет",
                "v": round(sum(over)),
                "save": 0,
                "d": f"За 30 днів: {len(over)} днів понад {daily_budget} грн, у середньому на {sum(over) / len(over):.0f} грн більше.",
            }
        )
    return out


def goods(rows, daily_budget):
    out = []
    add = lambda key, t, d, v=None: out.append(
        {"id": key, "t": t, "v": round(_sum(rows, key)) if v is None else v, "d": d}
    )

    if _sum(rows, "saved"):
        add(
            "saved",
            "🫙 Відкладаєш",
            "Гроші йдуть у банку «На уник», округлення працює саме. Так тримати — це основа подушки.",
        )
    if _sum(rows, "bills"):
        add(
            "bills",
            "🏠 Оренда й комуналка",
            "Обов'язкові платежі відкладаєш окремими банками — вони під контролем і не з'їдаються дрібницями.",
        )
    if 0 < _sum(rows, "transport") < 800:
        add("transport", "🚇 Транспорт", "Метро й автобуси, таксі рідко — тут усе ощадливо.")
    big = [r for r in _period(rows, "big") if _filter("big")(r)]
    if big:
        add(
            "big",
            "🛒 Супермаркети",
            f"Великих закупів: {len(big)}. Так вигідніше, ніж у магазині під домом — роби так частіше.",
        )
    if _sum(rows, "health"):
        add("health", "💊 Здоров'я", "Це не зайві витрати, на здоров'ї не економимо.")
    ok = 30 - sum(1 for v in _spend_days(rows).values() if v > daily_budget)
    if ok >= 5:
        out.append(
            {
                "id": None,
                "t": "✅ Дні в межах бюджету",
                "v": None,
                "d": f"{ok} днів із 30 не вийшли за {daily_budget} грн. Мета — щоб таких було більше половини.",
            }
        )
    return out


def details(key, daily_budget=280):
    rows = list(_read_csv().values())
    if key == "over":
        days = sorted((d, v) for d, v in _spend_days(rows).items() if v > daily_budget)
        items = [
            [f"{d[8:10]}.{d[5:7]}", f"на {v - daily_budget:.0f} грн понад бюджет", round(-v)] for d, v in days[::-1]
        ]
    else:
        f = _filter(key, rows)
        found = sorted((r for r in _period(rows, key) if f(r)), key=lambda r: r["time"], reverse=True)
        items = [
            [f"{r['time'][8:10]}.{r['time'][5:7]} {r['time'][11:16]}", r["description"][:40], round(_own(r))]
            for r in found
        ]
    return {"id": key, "items": items[:40], "count": len(items), "total": round(sum(i[2] for i in items))}


def plan(start, shops, limit):
    # from `start` on: ready food (the shops near home and cafes) within a monthly limit,
    # and how much less goes out a day than in the 30 days before
    start = dt.date.fromisoformat(start)
    today = dt.date.today()
    spent, food = {}, {}
    for r in _read_csv().values():
        if not _is_daily_spend(r):
            continue
        d = r["time"][:10]
        spent[d] = spent.get(d, 0) - float(r["amount"])
        if int(r["mcc"] or 0) in CAFE or _has(r, *shops):
            food[d] = food.get(d, 0) - float(r["amount"])
    on = lambda m, d: m.get(d.isoformat(), 0)  # noqa: E731
    usual = sum(on(spent, start - dt.timedelta(days=i)) for i in range(1, 31)) / 30
    days = [start + dt.timedelta(days=i) for i in range((today - start).days + 1)]

    month_len = calendar.monthrange(today.year, today.month)[1]
    first = max(start, today.replace(day=1))
    month_limit = limit * (month_len - first.day + 1) / month_len  # a plan started mid-month gets its share
    month_food = sum(on(food, first + dt.timedelta(days=i)) for i in range((today - first).days + 1))
    return {
        "start": _date(start),
        "days": len(days),
        "usual": round(usual),
        "saved": round(usual * len(days) - sum(on(spent, d) for d in days)),
        "today": round(on(spent, today)),
        "food_today": round(on(food, today)),
        "norm": round(limit / month_len),
        "month_food": round(month_food),
        "month_limit": round(month_limit),
        "left": round(month_limit - month_food),
        "days_left": month_len - today.day,
    }
