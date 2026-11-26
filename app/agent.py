# runs on the laptop when the bot lives on the hub:
# - laptop state every 20 s and calls from the hub (modes, lamp)
# - eco on its own when the laptop is on battery and the hub is out of reach
# - the whole bot (python -m app) when the hub has been silent for 10 min, stopped when it is back
import os
import socket
import subprocess
import sys
import threading
import time

from app import config, handover, link, relay, sync
from app.features import light, money
from app.log import log
from app.main import SINGLE_INSTANCE_PORT as BOT_PORT
from app.paths import HEARTBEAT, ROOT, TAKEOVER_HEARTBEAT
from app.services import laptop
from app.telegram import Ctx

SINGLE_INSTANCE_PORT = BOT_PORT + 1  # the bot itself takes main's port when it covers for the hub
LOOP_SEC = 5
STATE_SEC = 20
TAKEOVER_SEC = 10 * 60
HUNG_SEC = 6 * 60  # the bot here stopped writing its heartbeat: stuck, restart it
RESUME_SEC = 15  # retained hub beat and takeover flag come right after connecting
SELF_ECO_SEC = 2 * 60
NO_WINDOW = 0x08000000
LAMP_METHODS = {"state", "power", "toggle", "bright", "ct", "rgb"}

_hub = {"ts": 0.0}
_was_covering = {"on": False}  # retained flag: the laptop ran the bot before this agent started
_self_eco = {"since": None, "done": False}


def _lamp(method: str, *args):
    if method not in LAMP_METHODS:
        raise ValueError(f"lamp: {method}?")
    return light.with_lamp(lambda lamp: getattr(lamp, method)(*args))


CALLS = {"run_mode": laptop.run_mode, "lamp": _lamp}


def _on_call(msg: dict) -> None:
    if not relay.fresh(msg) or msg.get("c") not in CALLS:
        return

    def run():
        try:
            reply = {"re": msg["id"], "result": CALLS[msg["c"]](*msg.get("a", []))}
        except Exception as e:
            reply = {"re": msg["id"], "error": str(e)}
        try:
            relay.send(link.REPLY, reply)
        except Exception as e:
            log(f"агент, відповідь: {e!r}")
        if msg["c"] == "run_mode":
            _publish_state()

    threading.Thread(target=run, daemon=True).start()


def _publish_state() -> bool:
    try:
        relay.send(link.STATE, {**laptop.snapshot(), "ts": time.time()}, retain=True)
        return True
    except Exception:
        return False  # not connected yet or no internet: the hub sees the state get old


def _hub_alive() -> bool:
    return time.time() - _hub["ts"] < TAKEOVER_SEC


def _self_eco_check() -> None:
    # outage without internet: the hub can not switch the laptop, so do it here
    ac, _ = laptop.power()
    hub_reachable = relay.connected() and time.time() - _hub["ts"] < 3 * link.HUB_BEAT_SEC
    if ac or hub_reachable:
        if ac and _self_eco["done"] and laptop.is_eco():
            laptop.run_mode("normal")
            log("агент: світло є, повернув звичайний")
        _self_eco.update(since=None, done=False)
        return
    _self_eco["since"] = _self_eco["since"] or time.time()
    cfg = config.load()
    if (
        not _self_eco["done"]
        and time.time() - _self_eco["since"] > SELF_ECO_SEC
        and cfg.get("station_auto_eco", True)
        and not laptop.is_eco()
    ):
        laptop.run_mode("eco")
        _self_eco["done"] = True
        log("агент: ноут на батареї, хаба не чути — увімкнув еко")


class Takeover:
    def __init__(self):
        self.active = False
        self.proc = None
        self.started = time.time()
        self.since = self.started
        self.retry_at = 0.0

    def _say(self, text: str) -> None:
        cfg = config.load()
        if cfg.get("chat_id"):
            try:
                Ctx(cfg["token"], cfg["chat_id"], cfg).send(text)
            except Exception as e:
                log(f"агент: {e!r}")

    def _flag(self, on: bool) -> None:
        try:
            relay.send(link.TAKEOVER, {"on": on, "ts": time.time()}, retain=True)
        except Exception as e:
            log(f"агент, takeover: {e!r}")

    def _spawn(self) -> None:
        self.retry_at = time.time() + 60
        TAKEOVER_HEARTBEAT.write_text(str(int(time.time())))
        env = {**os.environ, "HOME_BOT_ROLE": "standalone", "HOME_BOT_TAKEOVER": "1"}
        out = open(ROOT / "takeover_out.log", "a")
        # stdin is a pipe: the bot exits when this process is gone, so it never stays alone
        self.proc = subprocess.Popen(
            [sys.executable, "-X", "faulthandler", "-m", "app"],
            cwd=ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=out,
            stderr=out,
            creationflags=NO_WINDOW,
        )

    def start(self, resume: bool = False) -> None:
        log("агент: продовжую підміну хаба" if resume else "агент: хаба не чути 10 хв, запускаю бота тут")
        self.active = True
        # what the bot here collects from this moment goes to the hub when it is back
        self.since = time.time() - (handover.RESUMED_SINCE_SEC if resume else 0)
        self._flag(True)
        self._spawn()
        if not resume:
            self._say("⚠️ ПК з ботом не на зв'язку 10 хв — бот тимчасово працює з ноута.")

    def stop(self) -> None:
        log("агент: хаб повернувся, зупиняю бота тут")
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(30)
        self.proc = None
        self.active = False
        sync.push(force=True)  # the hub takes the state from here
        try:
            handover.send_data(self.since)
        except Exception as e:
            log(f"агент, handover: {e!r}")
        self._flag(False)
        self._say("✅ ПК з ботом знову на зв'язку — бот повернувся туди.")

    def check(self) -> None:
        if self.active and _hub_alive():
            self.stop()
        elif not self.active and not _hub_alive() and relay.connected():
            # after a restart of the agent (watchdog, reboot) go on covering at once, no 10 min gap
            if _was_covering["on"] and time.time() - self.started > RESUME_SEC:
                self.start(resume=True)
            elif time.time() - self.started > TAKEOVER_SEC:
                self.start()
        elif self.active and self.proc.poll() is not None and time.time() > self.retry_at:
            log(f"агент: бот на ноуті зупинився ({self.proc.returncode}), перезапуск")
            self._spawn()
        elif self.active and time.time() - TAKEOVER_HEARTBEAT.stat().st_mtime > HUNG_SEC:
            log("агент: бот на ноуті завис, перезапуск")
            self.proc.kill()
            self.proc.wait(30)
            self._spawn()


def _single_instance() -> socket.socket:
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
    except OSError:
        log("агент вже запущений, вихід")
        sys.exit(0)
    return sock


def main() -> None:
    lock = _single_instance()  # noqa: F841 keep the port busy while running
    log("агент: старт")
    relay.connect(will=(link.STATE, {"ts": 0}))
    relay.on(link.CALL, _on_call)
    relay.on(link.HUB, lambda msg: msg.get("ts") and _hub.update(ts=msg["ts"]))  # ts 0 is the will
    relay.on(link.TAKEOVER, lambda msg: _was_covering.update(on=bool(msg.get("on"))))
    sync.start()
    takeover = Takeover()
    published = 0.0
    last_ac = None
    while True:
        HEARTBEAT.write_text(str(int(time.time())))
        try:
            ac = laptop.power()[0]
            if (time.time() - published > STATE_SEC or ac != last_ac) and _publish_state():
                published, last_ac = time.time(), ac
            takeover.check()
            if not takeover.active:  # the bot covering for the hub does both itself
                _self_eco_check()
                money.tick(None)  # statement for the tools on the laptop (mono_dir)
        except Exception as e:
            log(f"агент: {e!r}")
        time.sleep(LOOP_SEC)
