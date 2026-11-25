# dtek outage schedule (kyiv region) from github.com/Baskerville42/outage-data-ua
# per hour: yes, no, first / second (first or second half hour off), maybe* (possible)

import datetime as dt
import json
import time
import urllib.request

from app.paths import ROOT

CACHE = ROOT / "outage_cache.json"
STATE = ROOT / "outage_state.json"
URL = "https://raw.githubusercontent.com/Baskerville42/outage-data-ua/main/data/kyiv-region.json"
REFRESH_SEC = 15 * 60

# minutes before an outage -> reminder key
WARN = ((60, "h1"), (10, "m10"))

_last_fetch = 0


def fetch(force=False):
    global _last_fetch
    if not force and time.time() - _last_fetch < REFRESH_SEC and CACHE.exists():
        return
    _last_fetch = time.time()
    req = urllib.request.Request(URL, headers={"User-Agent": "home-bot"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read()
    json.loads(data)  # make sure it is json
    CACHE.write_bytes(data)


def _load():
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def updated_text():
    return (_load().get("fact") or {}).get("update", "")


def intervals(group):
    # [(start, end, maybe)] for the group, sorted, neighbours merged
    key = f"GPV{group}"
    out = []
    days = (_load().get("fact") or {}).get("data") or {}
    for ts, groups in days.items():
        hours = groups.get(key)
        if not hours:
            continue
        day = dt.datetime.fromtimestamp(int(ts))
        for h in range(1, 25):
            v = str(hours.get(str(h), "yes"))
            start = day + dt.timedelta(hours=h - 1)
            maybe = v.startswith("maybe") or v.startswith("m")
            base = v[5:] if v.startswith("maybe") else (v[1:] if v in ("mfirst", "msecond") else v)
            if base in ("no", ""):
                seg = (start, start + dt.timedelta(hours=1))
            elif base == "first":
                seg = (start, start + dt.timedelta(minutes=30))
            elif base == "second":
                seg = (start + dt.timedelta(minutes=30), start + dt.timedelta(hours=1))
            else:
                continue
            if out and out[-1][1] == seg[0] and out[-1][2] == maybe:
                out[-1] = (out[-1][0], seg[1], maybe)
            else:
                out.append((seg[0], seg[1], maybe))
    out.sort()
    return out


def current_and_next(group, now=None):
    now = now or dt.datetime.now()
    cur = nxt = None
    for s, e, maybe in intervals(group):
        if s <= now < e:
            cur = (s, e, maybe)
        elif s > now and nxt is None:
            nxt = (s, e, maybe)
    return cur, nxt


def _hm(t):
    return t.strftime("%H:%M") if t.time() != dt.time(0) else "24:00"


def schedule_text(group, now=None):
    if not group:
        return "🗓 Черга не вказана. Напиши: черга 3.1"
    now = now or dt.datetime.now()
    items = [x for x in intervals(group) if x[1] > now]
    if not _load():
        return "🗓 Графік ще не завантажився."
    lines = [f"🗓 Графік, черга {group}:"]
    if not items:
        lines.append("відключень не заплановано ✅")
    day = None
    for s, e, maybe in items:
        d = s.date()
        if d != day:
            day = d
            lines.append(
                (
                    "Сьогодні"
                    if d == now.date()
                    else "Завтра"
                    if d == now.date() + dt.timedelta(days=1)
                    else d.strftime("%d.%m")
                )
                + ":"
            )
        end = "24:00" if e.date() > s.date() and e.time() == dt.time(0) else e.strftime("%H:%M")
        lines.append(f"  ⚫ {s:%H:%M}–{end}" + (" (можливо)" if maybe else ""))
    upd = updated_text()
    if upd:
        lines.append(f"Оновлено ДТЕК: {upd}")
    return "\n".join(lines)


def _state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def due_warnings(group, now=None):
    # [(key, minutes_left, (start, end, maybe))] that are due now
    if not group:
        return []
    now = now or dt.datetime.now()
    sent, out = _state(), []
    for s, e, maybe in intervals(group):
        if s <= now:
            continue
        mins = (s - now).total_seconds() / 60
        for lead, tag in WARN:
            k = f"{tag}:{s:%Y-%m-%dT%H:%M}"
            if mins <= lead and k not in sent:
                # missed the hour one (bot was off) and 10 min or less left: send only the 10 min one
                if tag == "h1" and mins <= WARN[1][0]:
                    continue
                out.append((k, int(round(mins)), (s, e, maybe)))
    return out


def mark(key):
    s = _state()
    s[key] = time.time()
    cutoff = time.time() - 3 * 86400
    s = {k: v for k, v in s.items() if v > cutoff}
    STATE.write_text(json.dumps(s, indent=1), encoding="utf-8")
