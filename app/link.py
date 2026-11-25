# hub <-> laptop agent over the same mqtt as the panel.
# the agent reports the laptop state and runs calls for the hub (modes, lamp),
# the hub beats every minute so the agent knows when to cover for it.
import threading
import time
import uuid

from app import relay
from app.log import log

CALL = ("agent", "call")
REPLY = ("agent", "reply")
STATE = ("agent", "state")  # retained, {"ts": 0} when the agent drops off
TAKEOVER = ("agent", "takeover")  # retained, {"on": true} while the laptop runs the bot for the hub
HUB = ("hub", "alive")  # retained, {"ts": 0} when the hub drops off

STATE_MAX_AGE = 90
HUB_BEAT_SEC = 60
TAKEOVER_WAIT_SEC = 180

OFFLINE = "ноут не на зв'язку (вимкнений, спить або без інтернету)"

_state = {}
_takeover = {}
_pending = {}


def laptop_state() -> dict | None:
    if time.time() - _state.get("ts", 0) > STATE_MAX_AGE:
        return None
    return _state


def call(name: str, *args, timeout: float = 30):
    if not laptop_state():
        raise RuntimeError(OFFLINE)
    cid = uuid.uuid4().hex
    _pending[cid] = entry = [threading.Event(), None]
    try:
        relay.send(CALL, {"c": name, "a": list(args), "id": cid, "ts": int(time.time() * 1000)})
        if not entry[0].wait(timeout):
            raise RuntimeError("ноут не відповів")
    finally:
        _pending.pop(cid, None)
    if "error" in entry[1]:
        raise RuntimeError(entry[1]["error"])
    return entry[1].get("result")


class Lamp:
    # same methods as services.lamp, run on the laptop
    def __getattr__(self, method: str):
        return lambda *args: call("lamp", method, *args)


def _on_reply(msg: dict) -> None:
    entry = _pending.get(msg.get("re"))
    if entry:
        entry[1] = msg
        entry[0].set()


def _on_state(msg: dict) -> None:
    _state.clear()
    _state.update(msg)


def _beat() -> None:
    while True:
        try:
            relay.send(HUB, {"ts": time.time()}, retain=True)
            time.sleep(HUB_BEAT_SEC)
        except Exception:
            time.sleep(5)  # not connected yet or no internet: the broker shows the will meanwhile


def start_hub() -> None:
    relay.connect(will=(HUB, {"ts": 0}))
    relay.on(REPLY, _on_reply)
    relay.on(STATE, _on_state)
    relay.on(TAKEOVER, _takeover.update)
    threading.Thread(target=_beat, daemon=True, name="hub_beat").start()
    # the laptop may be running the bot right now: it stops when it sees the beat, then we start
    deadline = time.time() + TAKEOVER_WAIT_SEC
    time.sleep(5)  # retained messages come right after connecting
    while _takeover.get("on") and time.time() < deadline:
        time.sleep(2)
    if _takeover.get("on"):
        log("ноут досі тримає бота, стартую все одно")
