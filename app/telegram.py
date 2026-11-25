import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass

from app.keyboards import DEFAULT
from app.log import log

# per thread: when set, answers go there instead of the chat (used by the panel)
_local = threading.local()


def api(token: str, method: str, **params) -> dict:
    data = urllib.parse.urlencode(
        {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in params.items()}
    ).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 429:  # flood limit: wait as telegram asks
            wait = json.loads(e.read().decode()).get("parameters", {}).get("retry_after", 30)
            log(f"429, wait {wait}s")
            time.sleep(min(wait, 120))
        raise


@contextmanager
def redirected(target):
    _local.target = target
    try:
        yield
    finally:
        _local.target = None


@dataclass
class Ctx:
    token: str
    chat: int
    cfg: dict

    def send(self, text: str, inline: list | None = None, menu: dict | None = None) -> None:
        target = getattr(_local, "target", None)
        if target:
            return target(text, inline or [])
        markup = {"inline_keyboard": inline} if inline else (menu or DEFAULT)
        flush(self.token)
        try:
            api(self.token, "sendMessage", chat_id=self.chat, text=text, reply_markup=markup)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code != 429:
                raise
            # no internet, e.g. the router is rebooting at the start of an outage: send later
            with _outbox_lock:
                _outbox.append((time.time(), self.chat, text, markup))
                del _outbox[:-OUTBOX_MAX]


OUTBOX_MAX = 20
_outbox = []
_outbox_lock = threading.Lock()


def flush(token: str) -> None:
    with _outbox_lock:
        while _outbox:
            ts, chat, text, markup = _outbox[0]
            stamp = time.strftime("%H:%M", time.localtime(ts))
            try:
                api(token, "sendMessage", chat_id=chat, text=f"🕒 {stamp}\n{text}", reply_markup=markup)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    return
                log(f"outbox: drop message, {e!r}")
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                return
            _outbox.pop(0)
