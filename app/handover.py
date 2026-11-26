# laptop -> hub over the broker, no files to carry and nobody at the hub pc:
# - data the laptop collected while it covered for the hub (power log, sleep cache), sent when the hub is back
# - secrets on demand: python -m app.handover secrets   (.env and the xiaomi session, written on the hub)
import json
import sys
import threading
import time
import uuid

from app import relay
from app.log import log
from app.paths import ROOT
from app.services import powerlog

TOPIC = ("handover",)
ACK = ("handover", "ack")
SECRETS = [".env", "xiaomi_session.json"]
SLEEP_CACHE = "sleep_cache.json"
RESUMED_SINCE_SEC = 2 * 86400  # the start of a cover the agent did not see: look back this far


def _msg(**body) -> dict:
    return {**body, "id": uuid.uuid4().hex, "ts": int(time.time() * 1000)}


def send_data(since: float) -> None:
    # agent, right after the hub came back
    body = {"power": powerlog.export(since)}
    if (ROOT / SLEEP_CACHE).exists():
        body["sleep"] = (ROOT / SLEEP_CACHE).read_text(encoding="utf-8")
    relay.send(TOPIC, _msg(**body))
    log(f"handover: на хаб {len(body['power']['samples'])} записів живлення")


def send_secrets(wait: float = 60) -> list | None:
    # the hub answers with the names it wrote, None if it did not answer
    got = threading.Event()
    answer = {}
    msg = _msg(files={n: (ROOT / n).read_text(encoding="utf-8") for n in SECRETS if (ROOT / n).exists()})

    def on_ack(a: dict) -> None:
        if a.get("re") == msg["id"]:
            answer.update(a)
            got.set()

    relay.on(ACK, on_ack)
    relay.connect()
    deadline = time.time() + 15
    while not relay.connected() and time.time() < deadline:
        time.sleep(0.2)
    relay.send(TOPIC, msg)
    return answer.get("files") if got.wait(wait) else None


def _receive(msg: dict) -> None:
    if not relay.fresh(msg):
        return
    written = []
    for name, text in msg.get("files", {}).items():
        if name in SECRETS:
            (ROOT / name).write_text(text, encoding="utf-8")
            written.append(name)
    if "power" in msg:
        samples, events = powerlog.add(**msg["power"])
        log(f"handover: з ноута +{samples} записів живлення, +{events} подій")
    if "sleep" in msg:
        _take_sleep(msg["sleep"])
    if written:
        log(f"handover: оновлено {', '.join(written)}")
    relay.send(ACK, {"re": msg["id"], "files": written})


def _take_sleep(text: str) -> None:
    path = ROOT / SLEEP_CACHE
    mine = json.loads(path.read_text(encoding="utf-8")).get("ts", 0) if path.exists() else 0
    if json.loads(text).get("ts", 0) > mine:
        path.write_text(text, encoding="utf-8")


def start_hub() -> None:
    relay.on(TOPIC, _receive)


if __name__ == "__main__":
    if sys.argv[1:] == ["secrets"]:
        files = send_secrets()
        print(f"хаб записав: {', '.join(files)}" if files else "хаб не відповів (він працює? /where)")
    else:
        sys.exit("python -m app.handover secrets")
