# power log: a sample a minute (station, laptop, internet) and events in power.db
# summary: python -m app.services.powerlog [days]

import datetime as dt
import sqlite3
import sys
import threading
import time

from app.paths import ROOT

DB = ROOT / "power.db"
GAP_SEC = 15 * 60  # no samples longer than this: laptop slept or bot was down
MERGE_SEC = 10 * 60
# event -> is there an outage after it
OUTAGE_EVENTS = {"laptop_battery": True, "laptop_mains": False, "grid_lost": True, "grid_back": False}

SAMPLE_FIELDS = (
    "station_online",
    "pct",
    "mode",
    "ac_on",
    "ac_in",
    "dc_in",
    "in_w",
    "out_w",
    "laptop_ac",
    "laptop_pct",
    "eco",
    "net",
    "ups",
    "temp",
    "inv_temp",
)

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB, timeout=10)
    db.execute(f"create table if not exists samples (ts integer, {', '.join(SAMPLE_FIELDS)})")
    have = {r[1] for r in db.execute("pragma table_info(samples)")}
    for field in SAMPLE_FIELDS:
        if field not in have:
            db.execute(f"alter table samples add column {field}")
    db.execute("create table if not exists events (ts integer, kind text, detail text)")
    db.execute("create index if not exists samples_ts on samples (ts)")
    db.execute("create index if not exists events_ts on events (ts)")
    return db


SAMPLE_COLS = ", ".join(("ts",) + SAMPLE_FIELDS)


def export(since: float, db_path=None) -> dict:
    # samples and events from a moment on, to hand to another machine
    db = sqlite3.connect(db_path) if db_path else _connect()
    out = {
        "samples": db.execute(f"select {SAMPLE_COLS} from samples where ts >= ?", (int(since),)).fetchall(),
        "events": db.execute("select ts, kind, detail from events where ts >= ?", (int(since),)).fetchall(),
    }
    db.close()
    return out


def add(samples: list, events: list) -> tuple:
    # rows from another machine (the laptop that covered for the hub) this log does not have yet
    with _lock, _connect() as db:
        have_s = {r[0] for r in db.execute("select ts from samples")}
        have_e = {tuple(r) for r in db.execute("select ts, kind from events")}
        new_s = [list(r) for r in samples if r[0] not in have_s]
        new_e = [list(r) for r in events if (r[0], r[1]) not in have_e]
        marks = ", ".join("?" * (len(SAMPLE_FIELDS) + 1))
        db.executemany(f"insert into samples ({SAMPLE_COLS}) values ({marks})", new_s)
        db.executemany("insert into events (ts, kind, detail) values (?, ?, ?)", new_e)
    db.close()
    return len(new_s), len(new_e)


def merge(src) -> tuple:
    # another power.db file (a bundle)
    return add(**export(0, src))


def sample(ts: float | None = None, **fields) -> None:
    row = [int(ts or time.time())] + [fields.get(k) for k in SAMPLE_FIELDS]
    with _lock, _connect() as db:
        db.execute(f"insert into samples values ({', '.join('?' * len(row))})", row)
    db.close()


def event(kind: str, detail: str = "", ts: float | None = None) -> None:
    with _lock, _connect() as db:
        db.execute("insert into events values (?, ?, ?)", (int(ts or time.time()), kind, detail))
    db.close()


def _rows(since: float) -> tuple[list, list]:
    db = _connect()
    db.row_factory = sqlite3.Row
    samples = db.execute("select * from samples where ts >= ? order by ts", (since,)).fetchall()
    events = db.execute("select * from events where ts >= ? order by ts", (since,)).fetchall()
    db.close()
    return samples, events


def _no_grid(s: sqlite3.Row) -> bool | None:
    if s["station_online"] and s["mode"] == "charging":
        return False
    if s["station_online"] and s["mode"] == "supply":
        return True
    if s["laptop_ac"] is None:
        return None
    return not s["laptop_ac"]


def _hm(sec: float) -> str:
    h, m = divmod(int(sec) // 60, 60)
    return f"{h} год {m:02d} хв" if h else f"{m} хв"


def _t(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%d.%m %H:%M")


def _outages(samples: list, events: list) -> list[tuple[float, float]]:
    # laptop events cover the time it slept; laptop on mains right before the station starts
    # supplying is just plugging into the station, so short breaks are merged
    points = [(s["ts"], _no_grid(s)) for s in samples]
    points += [(e["ts"], OUTAGE_EVENTS[e["kind"]]) for e in events if e["kind"] in OUTAGE_EVENTS]
    out, start = [], None
    for ts, off in sorted(points, key=lambda p: p[0]):
        if off and start is None:
            start = ts
        elif off is False and start is not None:
            if out and start - out[-1][1] < MERGE_SEC:
                start = out.pop()[0]
            out.append((start, ts))
            start = None
    if start is not None:
        out.append((start, points and max(p[0] for p in points)))
    return out


def _rate(samples: list, mode: str) -> tuple[float, float] | None:
    # % per hour and average watts over continuous stretches of one station mode
    pct = hours = watts = n = 0
    run = []
    for s in samples + [None]:
        if s and s["station_online"] and s["mode"] == mode and (not run or s["ts"] - run[-1]["ts"] <= GAP_SEC):
            run.append(s)
            continue
        if len(run) > 1:
            pct += abs(run[-1]["pct"] - run[0]["pct"])
            hours += (run[-1]["ts"] - run[0]["ts"]) / 3600
        for r in run:
            watts += r["out_w"] if mode == "supply" else r["in_w"]
            n += 1
        run = [s] if s and s["station_online"] and s["mode"] == mode else []
    if hours < 0.25:
        return None
    return pct / hours, watts / n


def stats(days: int = 7) -> dict | None:
    samples, events = _rows(time.time() - days * 86400)
    if not samples:
        return None
    outages = _outages(samples, events)
    kinds = {}
    for e in events:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    return {
        "since": samples[0]["ts"],
        "outages": outages,
        "supply": _rate(samples, "supply"),
        "charging": _rate(samples, "charging"),
        "idle": _rate(samples, "idle"),
        "events": kinds,
        "ongoing": bool(_no_grid(samples[-1])) and time.time() - samples[-1]["ts"] < GAP_SEC,
    }


def battery(hours: int = 24, step: int = 15 * 60) -> list[tuple[int, int]]:
    # station % over time, one point per step, only while it was online
    samples, _ = _rows(time.time() - hours * 3600)
    out = {}
    for s in samples:
        if s["station_online"] and s["pct"] is not None:
            out[s["ts"] // step * step] = s["pct"]
    return sorted(out.items())


BLINK_SEC = 3 * 60  # shorter outages are just blinks, not listed one by one
DAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд")


def _day(ts: float) -> str:
    d = dt.datetime.fromtimestamp(ts)
    return f"{DAYS[d.weekday()]} {d:%d.%m}"


def _num(v: float) -> str:
    return f"{v:.1f}".replace(".", ",").replace(",0", "")


def summary(days: int = 7) -> str:
    st = stats(days)
    if not st:
        return "Даних ще немає."
    real = [(a, b) for a, b in st["outages"] if b - a >= BLINK_SEC]
    blinks = len(st["outages"]) - len(real)
    ongoing = st["ongoing"] and st["outages"]
    lines = [f"⚡ Світло за {days} днів", ""]
    if not real:
        lines.append("Відключень не було 🎉")
    else:
        total = sum(b - a for a, b in real)
        lines.append(f"Відключень: {len(real)}, без світла разом {_hm(total)}")
        a, b = max(real, key=lambda o: o[1] - o[0])
        lines.append(f"Найдовше: {_day(a)}, {_hm(b - a)}")
    if ongoing:
        lines.append(f"Зараз світла немає вже {_hm(time.time() - st['outages'][-1][0])}")
    if real:
        lines += ["", "Останні:"]
        for a, b in reversed(real[-6:]):
            end = "зараз" if ongoing and b == st["outages"][-1][1] else _t(b)[-5:]
            lines.append(f"• {_day(a)}, {_t(a)[-5:]}–{end} ({_hm(b - a)})")
    if blinks:
        lines.append(f"…і ще коротких блимань: {blinks}")
    if st["supply"] or st["charging"]:
        lines += ["", "🔋 Станція"]
    if r := st["supply"]:
        lines.append(f"Тримає ≈ {_num(85 / r[0])} год до 15% (ноут і роутер ≈ {r[1]:.0f} Вт, −{_num(r[0])}% за годину)")
    if r := st["charging"]:
        lines.append(f"Заряджається з нуля ≈ {_num(100 / r[0])} год (+{r[0]:.0f}% за годину)")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(summary(int(sys.argv[1]) if len(sys.argv) > 1 else 7))
