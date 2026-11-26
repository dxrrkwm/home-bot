# transport between the bot, the panel and the laptop agent, no server of our own:
# mqtt on a public broker first (no daily limits, retained state), ntfy.sh as a backup for the panel (250 messages a day per ip).
# everything is sealed with the key from the panel button url: commands too, so nobody else can send them.
import base64
import hmac
import json
import os
import threading
import time
import urllib.request
import uuid
import zlib

import paho.mqtt.client as mqtt

from app import config
from app.log import log

BROKER, BROKER_PORT = "broker.hivemq.com", 8883
NTFY = "https://ntfy.sh"
HEADERS = {"User-Agent": "home-bot"}
# state kinds: the broker keeps the last one for a panel that opens later
RETAINED = {"station", "lamp", "fin", "prices", "sleep", "band"}
MAX_AGE_SEC = 120  # older commands are replays or stuck in a queue

_local = threading.local()  # which channel the current command came from, the answer goes back the same way
_seen = []
_handlers = {}  # topic -> fn(message)
_mqtt = None


def _key() -> bytes:
    key = config.load()["panel_key"]
    return base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))


def _stream(key: bytes, nonce: bytes, size: int) -> bytes:
    return b"".join(hmac.digest(key, b"e" + nonce + i.to_bytes(4, "big"), "sha256") for i in range(size // 32 + 1))


def seal(obj) -> str:
    key = _key()
    data = zlib.compress(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode(), 9)
    nonce = os.urandom(16)
    body = bytes(a ^ b for a, b in zip(data, _stream(key, nonce, len(data))))
    tag = hmac.digest(key, b"m" + nonce + body, "sha256")[:16]
    return base64.b64encode(nonce + body + tag).decode()


def unseal(text: str):
    key, raw = _key(), base64.b64decode(text)
    nonce, body, tag = raw[:16], raw[16:-16], raw[-16:]
    if not hmac.compare_digest(hmac.digest(key, b"m" + nonce + body, "sha256")[:16], tag):
        raise ValueError("bad tag")
    return json.loads(zlib.decompress(bytes(a ^ b for a, b in zip(body, _stream(key, nonce, len(body))))))


def fresh(msg: dict) -> bool:
    # a command {"id": ..., "ts": ms} is taken once and only while it is fresh
    if abs(time.time() - msg.get("ts", 0) / 1000) > MAX_AGE_SEC or msg.get("id") in _seen:
        return False
    _seen.append(msg.get("id"))
    del _seen[:-500]
    return True


def _topic(*parts: str) -> str:
    return "/".join(["hb", config.load()["panel_topic"], *parts])


def connected() -> bool:
    return bool(_mqtt and _mqtt.is_connected())


def on(parts: tuple, fn) -> None:
    # fn gets every unsealed message on hb/<topic>/<parts>, retained ones too
    topic = _topic(*parts)
    _handlers[topic] = fn
    if connected():
        _mqtt.subscribe(topic, qos=1)


def send(parts: tuple, obj, retain: bool = False) -> None:
    if not connected():
        raise RuntimeError("немає зв'язку з mqtt")
    _mqtt.publish(_topic(*parts), seal(obj), qos=1, retain=retain)


def _on_message(client, userdata, m) -> None:
    fn = _handlers.get(m.topic)
    if not fn or not m.payload:
        return
    try:
        msg = unseal(m.payload.decode())
    except Exception:
        return
    try:
        fn(msg)
    except Exception as e:
        log(f"mqtt {m.topic.rsplit('/', 1)[-1]}: {e!r}")


def connect(will: tuple | None = None) -> None:
    # will: (parts, obj) the broker publishes (retained) when this client drops off
    global _mqtt
    if _mqtt or not config.load().get("panel_topic"):
        return
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"home-bot-{uuid.uuid4().hex[:8]}")
    client.tls_set()
    client.reconnect_delay_set(1, 60)
    if will:
        client.will_set(_topic(*will[0]), seal(will[1]), qos=1, retain=True)
    client.on_connect = lambda c, u, flags, rc, props: [c.subscribe(t, qos=1) for t in _handlers]
    client.on_message = _on_message
    client.connect_async(BROKER, BROKER_PORT, keepalive=60)
    client.loop_start()  # reconnects by itself, also after the internet comes back
    _mqtt = client


def publish(kind: str, obj) -> None:
    # data for the panel
    via = getattr(_local, "via", None) or ("mqtt" if connected() else "ntfy")
    if via == "mqtt":
        send(("d", kind), obj, retain=kind in RETAINED)
        return
    topic = config.load()["panel_topic"] + "d"
    req = urllib.request.Request(f"{NTFY}/{topic}", data=seal(obj).encode(), headers={**HEADERS, "Title": kind})
    urllib.request.urlopen(req, timeout=20).close()


def _handle(msg: dict, via: str, on_command) -> None:
    if not fresh(msg):
        return

    def run():
        _local.via = via
        try:
            on_command(msg["c"], msg["id"])
        except Exception as e:
            log(f"панель, команда: {e!r}")

    # own thread: a slow command must not hold the ones after it
    threading.Thread(target=run, daemon=True).start()


def _ntfy_loop(on_command) -> None:
    while True:
        try:
            topic = config.load()["panel_topic"]
            req = urllib.request.Request(f"{NTFY}/{topic}/json", headers=HEADERS)
            with urllib.request.urlopen(req, timeout=90) as r:
                for line in r:
                    ev = json.loads(line)
                    if ev.get("event") != "message":
                        continue
                    try:
                        msg = unseal(ev["message"])
                    except Exception:
                        continue
                    _handle(msg, "ntfy", on_command)
        except Exception as e:
            if "timed out" not in str(e):
                log(f"панель, ntfy: {e!r}")
                time.sleep(10)


def start(on_command) -> None:
    # panel commands
    connect()
    on(("cmd",), lambda msg: _handle(msg, "mqtt", on_command))
    threading.Thread(target=_ntfy_loop, args=(on_command,), daemon=True, name="ntfy").start()


def mqtt_connected() -> bool:
    return connected()
