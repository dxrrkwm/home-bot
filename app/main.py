import os
import socket
import sys
import threading
import time

from app import config, link, panel, relay, role, router, sync
from app.log import log
from app.paths import HEARTBEAT, TAKEOVER_HEARTBEAT
from app.telegram import Ctx, api, flush

SINGLE_INSTANCE_PORT = int(os.environ.get("HOME_BOT_PORT", 47321))


def _single_instance() -> socket.socket:
    # a second copy would poll telegram too and both would get 409
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
    except OSError:
        log("вже запущений, вихід")
        sys.exit(0)
    return sock


def _log_crashes() -> None:
    sys.excepthook = lambda t, e, tb: log(f"crash in main: {e!r}")
    threading.excepthook = lambda a: log(f"crash in {a.thread.name if a.thread else 'thread'}: {a.exc_value!r}")


def _exit_with_parent() -> None:
    # started by the laptop agent to cover for the hub: stdin closes when the agent is gone
    sys.stdin.read()
    log("агент зупинився, вихід")
    os._exit(0)


def _poll(offset: int) -> int:
    cfg = config.load()
    token = cfg.get("token", "").strip()
    if not token or "ВСТАВ" in token:
        time.sleep(15)
        return offset

    try:
        updates = api(token, "getUpdates", offset=offset, timeout=10).get("result", [])
    finally:
        # background work (power, station, reminders) must go on without internet,
        # messages it sends wait in the outbox
        if cfg.get("chat_id"):
            router.tick(Ctx(token, cfg["chat_id"], cfg))
        sync.push()

    flush(token)
    for upd in updates:
        offset = upd["update_id"] + 1
        try:
            router.handle_update(token, cfg, upd)
        except Exception as e:  # one bad command must not stop the bot
            log(f"помилка команди: {e!r}")
    return offset


def main() -> None:
    if role.ROLE == "agent":
        from app import agent

        agent.main()
        return

    lock = _single_instance()  # noqa: F841 keep the port busy while running
    _log_crashes()
    log(f"старт ({role.ROLE})")
    if os.environ.get("HOME_BOT_TAKEOVER"):
        threading.Thread(target=_exit_with_parent, daemon=True).start()
    sync.start()
    if role.ROLE == "hub":
        link.start_hub()
    else:
        relay.connect()
    sync.pull()
    router.start_background()
    panel.start()

    # watchdog restarts the bot if this gets old, the agent does it for the bot it started
    heartbeat = TAKEOVER_HEARTBEAT if os.environ.get("HOME_BOT_TAKEOVER") else HEARTBEAT
    offset = 0
    while True:
        heartbeat.write_text(str(int(time.time())))
        try:
            offset = _poll(offset)
        except Exception as e:  # no internet and so on: wait and retry
            log(f"мережа/помилка: {e!r}")
            time.sleep(10)
