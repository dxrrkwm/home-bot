# monthly reminders and birthdays from planner.json; if the laptop was off that day,
# the reminder comes when the bot starts (up to 3 days late)

import datetime as dt
import json

from app.paths import ROOT

STATE = ROOT / "planner_state.json"
PLAN = ROOT / "planner.json"  # personal, not in git: see planner.example.json
SEND_FROM_HOUR = 10  # do not bother before 10:00


def _plan():
    try:
        return json.loads(PLAN.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _load():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(s):
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


def due(now=None):
    # [(key, text, every)] that are due now; every: minutes between pings until "done", 0 for one message
    now = now or dt.datetime.now()
    if now.hour < SEND_FROM_HOUR:
        return []
    today, out, plan = now.date(), [], _plan()
    for m in plan.get("monthly", []):
        d = today.replace(day=m["day"])
        if d <= today <= d + dt.timedelta(days=3):
            out.append((f"{m['id']}:{d:%Y-%m}", m["text"], m.get("every", 0)))
    for b in plan.get("birthdays", []):
        who, key = b["who"], b["id"]
        for year in (today.year, today.year + 1):
            bd = dt.date(year, b["month"], b["day"])
            week = bd - dt.timedelta(days=7)
            if week <= today < bd and today <= week + dt.timedelta(days=3):
                out.append(
                    (
                        f"bd-week:{key}:{year}",
                        f"🎁 Через {(bd - today).days} дн. ({bd:%d.%m}) день народження {who}. Час подумати про подарунок.",
                        0,
                    )
                )
            if bd <= today <= bd + dt.timedelta(days=1):
                out.append((f"bd-day:{key}:{year}", f"🎉 Сьогодні день народження {who}! Привітай.", 0))
    return out


def pending(now=None):
    sent = _load()
    return [x for x in due(now) if x[0] not in sent]


def mark(key):
    s = _load()
    s[key] = dt.datetime.now().isoformat(timespec="seconds")
    # keep the file small
    if len(s) > 200:
        s = dict(sorted(s.items(), key=lambda kv: kv[1])[-150:])
    _save(s)
