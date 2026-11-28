# state files follow the machine that runs the bot: the hub, or the laptop while it covers for the hub.
# the running one keeps a sealed copy on the broker (retained), the next one to start takes it if it is newer.
import hashlib
import time

from app import config, relay
from app.log import log
from app.paths import ROOT

TOPIC = ("sync",)
FILES = [
    "reminders.json",
    "meds.json",
    "meds_state.json",
    "planner.json",
    "planner_state.json",
    "prices.json",
    "prices_state.json",
    "outage_state.json",
    "station_state.json",
    "light_state.json",
    "sleep_state.json",
    "mute_state.json",
    "presence_state.json",
    "digest_state.json",
    "band_state.json",
    "shifts.json",
    "money_state.json",
]
# settings that change from the chat
CONFIG_KEYS = [
    "chat_id",
    "station_ups",
    "station_auto_eco",
    "outage_group",
    "daily_budget",
    "panel_version",
    "pay",
    "steps_goal",
]
PUSH_SEC = 60

_last = {"sig": None, "at": 0.0}
_retained = {}


def _files() -> dict:
    return {name: (ROOT / name).read_text(encoding="utf-8") for name in FILES if (ROOT / name).exists()}


def _local_ts() -> float:
    return max(((ROOT / name).stat().st_mtime for name in FILES if (ROOT / name).exists()), default=0)


def start() -> None:
    relay.on(TOPIC, lambda msg: _retained.update(snap=msg))


def pull(wait: float = 8) -> bool:
    deadline = time.time() + wait
    while "snap" not in _retained and time.time() < deadline:
        time.sleep(0.2)
    snap = _retained.get("snap")
    if not snap or snap["ts"] <= _local_ts():
        return False
    for name, text in snap["files"].items():
        if name in FILES:
            (ROOT / name).write_text(text, encoding="utf-8")
    cfg = config.load()
    cfg.update({k: v for k, v in snap["cfg"].items() if k in CONFIG_KEYS})
    config.save(cfg)
    log(f"стан підтягнуто з брокера ({time.strftime('%d.%m %H:%M', time.localtime(snap['ts']))})")
    return True


def push(force: bool = False) -> None:
    if not force and time.time() - _last["at"] < PUSH_SEC:
        return
    _last["at"] = time.time()
    cfg = config.load()
    snap = {"files": _files(), "cfg": {k: cfg[k] for k in CONFIG_KEYS if k in cfg}}
    sig = hashlib.sha256(repr(snap).encode()).hexdigest()
    if sig == _last["sig"] and not force:
        return
    try:
        relay.send(TOPIC, {**snap, "ts": _local_ts()}, retain=True)
        _last["sig"] = sig
    except Exception as e:
        log(f"sync: {e!r}")
