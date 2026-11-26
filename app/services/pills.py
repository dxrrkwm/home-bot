# pills: schedule, "taken" marks, repeat every 20 min until marked
# stored in meds.json (list) and meds_state.json (marks per dose)

import datetime as dt
import json
import re
import uuid

from app.paths import ROOT

MEDS = ROOT / "meds.json"
STATE = ROOT / "meds_state.json"
REPEAT_MIN = 20  # repeat until marked
GIVE_UP_HOURS = 8  # stop reminding after this

ADD_RE = re.compile(
    r"^\s*(?:таблетк[аи]|ліки|лекарство|пігулка|pill)\s+(?P<name>.+?)\s+"
    r"(?P<times>\d{1,2}[:.]\d{2}(?:\s*(?:,|і|и|та)?\s*\d{1,2}[:.]\d{2})*)"
    r"(?:\s+(?:щодня|кожен день|каждый день))?"
    r"(?:\s+(?:кожні|кожен|каждые|каждый|раз на)\s+(?P<every>\d+)\s*(?:дн\w*|день|дня))?\s*$",
    re.I,
)


def _load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def meds():
    return _load(MEDS, [])


def parse_add(text):
    m = ADD_RE.match(text or "")
    if not m:
        return None
    times = []
    for h, mi in re.findall(r"(\d{1,2})[:.](\d{2})", m.group("times")):
        h, mi = int(h), int(mi)
        if h > 23 or mi > 59:
            return None
        times.append(f"{h:02d}:{mi:02d}")
    every = int(m.group("every") or 1)
    return {"name": m.group("name").strip(), "times": sorted(set(times)), "every": max(1, every)}


def add(med, today=None):
    items = meds()
    med = {
        "id": uuid.uuid4().hex[:6],
        **med,
        "start": (today or dt.date.today()).isoformat(),
        "added": dt.datetime.now().isoformat(timespec="minutes"),
    }
    items.append(med)
    _save(MEDS, items)
    return med


def delete(med_id):
    items = meds()
    left = [m for m in items if m["id"] != med_id]
    _save(MEDS, left)
    return len(left) != len(items)


def freq_text(m):
    return "щодня" if m.get("every", 1) == 1 else f"кожні {m['every']} дні"


def list_text():
    items = meds()
    if not items:
        return (
            "💊 Таблеток поки немає.\n\nДодай повідомленням, напр.:\n"
            "таблетка Вітамін D 09:00\nтаблетка Назва 09:00, 21:00\nтаблетка Назва 10:00 кожні 2 дні"
        )
    lines = [f"• {m['name']} — {', '.join(m['times'])}, {freq_text(m)}" for m in items]
    return "💊 Таблетки:\n" + "\n".join(lines) + "\n\nДодати ще: «таблетка Назва 09:00». Видалити — кнопкою нижче."


def is_day(m, day):
    start = dt.date.fromisoformat(m.get("start", day.isoformat()))
    return day >= start and (day - start).days % m.get("every", 1) == 0


def _key(m, day, t):
    return f"{m['id']}|{day.isoformat()}|{t}"


def due(now=None):
    # doses to remind about now: [(key, name, time, repeat)]
    now = now or dt.datetime.now()
    state = _load(STATE, {})
    out = []
    for m in meds():
        for day in (now.date() - dt.timedelta(days=1), now.date()):
            if not is_day(m, day):
                continue
            for t in m["times"]:
                h, mi = map(int, t.split(":"))
                at = dt.datetime.combine(day, dt.time(h, mi))
                if m.get("added") and at < dt.datetime.fromisoformat(m["added"]):
                    continue  # doses before the pill was added do not count
                if not (at <= now <= at + dt.timedelta(hours=GIVE_UP_HOURS)):
                    continue
                k = _key(m, day, t)
                st = state.get(k, {})
                if st.get("done"):
                    continue
                last = st.get("last_ping")
                if last is None or (now - dt.datetime.fromisoformat(last)).total_seconds() >= REPEAT_MIN * 60:
                    out.append((k, m["name"], t, last is not None))
    return out


def pinged(key, now=None):
    s = _load(STATE, {})
    s.setdefault(key, {})["last_ping"] = (now or dt.datetime.now()).isoformat(timespec="seconds")
    _cleanup(s)
    _save(STATE, s)


def mark(key, how="taken", now=None):
    s = _load(STATE, {})
    s.setdefault(key, {}).update(done=how, at=(now or dt.datetime.now()).isoformat(timespec="seconds"))
    _cleanup(s)
    _save(STATE, s)


def name_of(key):
    mid = key.split("|")[0]
    return next((m["name"] for m in meds() if m["id"] == mid), "таблетка")


def _cleanup(s):
    cutoff = (dt.date.today() - dt.timedelta(days=14)).isoformat()
    for k in [k for k in s if k.split("|")[1] < cutoff]:
        del s[k]
