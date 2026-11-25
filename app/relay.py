# transport between the bot and the panel, no server of our own:
# mqtt on a public broker first (no daily limits, retained state), ntfy.sh as a backup (250 messages a day per ip).
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
RETAINED = {"station", "lamp", "fin", "prices"}
MAX_AGE_SEC = 120  # older commands are replays or stuck in a queue

_local = threading.local()  # which channel the current command came from, the answer goes back the same way
_seen = []
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


def _topic(*parts: str) -> str:
    return "/".join(["hb", config.load()["panel_topic"], *parts])


def publish(kind: str, obj) -> None:
    via = getattr(_local, "via", None) or ("mqtt" if _mqtt and _mqtt.is_connected() else "ntfy")
    payload = seal(obj)
    if via == "mqtt":
        _mqtt.publish(_topic("d", kind), payload, qos=1, retain=kind in RETAINED)
        return
    topic = config.load()["panel_topic"] + "d"
    req = urllib.request.Request(f"{NTFY}/{topic}", data=payload.encode(), headers={**HEADERS, "Title": kind})
    urllib.request.urlopen(req, timeout=20).close()


def _accept(text: str) -> dict | None:
    # a sealed {"c": command, "id": ..., "ts": ms} that is fresh and seen once
    try:
        msg = unseal(text)
    except Exception:
        return None
    if abs(time.time() - msg.get("ts", 0) / 1000) > MAX_AGE_SEC or msg.get("id") in _seen:
        return None
    _seen.append(msg["id"])
    del _seen[:-500]
    return msg


def _handle(text: str, via: str, on_command) -> None:
    msg = _accept(text)
    if not msg:
        return

    def run():
        _local.via = via
        try:
            on_command(msg["c"], msg["id"])
        except Exception as e:
            log(f"панель, команда: {e!r}")

    # own thread: a slow command must not hold the ones after it
    threading.Thread(target=run, daemon=True).start()


def _start_mqtt(on_command) -> None:
    global _mqtt
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"home-bot-{uuid.uuid4().hex[:8]}")
    client.tls_set()
    client.reconnect_delay_set(1, 60)
    client.on_connect = lambda c, u, flags, rc, props: c.subscribe(_topic("cmd"), qos=1)
    client.on_message = lambda c, u, m: _handle(m.payload.decode(), "mqtt", on_command)
    client.connect_async(BROKER, BROKER_PORT, keepalive=60)
    client.loop_start()  # reconnects by itself, also after the internet comes back
    _mqtt = client


def _ntfy_loop(on_command) -> None:
    while True:
        try:
            topic = config.load()["panel_topic"]
            req = urllib.request.Request(f"{NTFY}/{topic}/json", headers=HEADERS)
            with urllib.request.urlopen(req, timeout=90) as r:
                for line in r:
                    ev = json.loads(line)
                    if ev.get("event") == "message":
                        _handle(ev["message"], "ntfy", on_command)
        except Exception as e:
            if "timed out" not in str(e):
                log(f"панель, ntfy: {e!r}")
                time.sleep(10)


def start(on_command) -> None:
    _start_mqtt(on_command)
    threading.Thread(target=_ntfy_loop, args=(on_command,), daemon=True, name="ntfy").start()


def mqtt_connected() -> bool:
    return bool(_mqtt and _mqtt.is_connected())
