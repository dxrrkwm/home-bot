# the xiaomi smart band through the mi fitness cloud (the same api the app uses): sleep, steps, heart rate
# protocol from github.com/LilZeeCN/mi-health-exporter, login from .env (XIAOMI_USER / XIAOMI_PASSWORD)
#   python -m app.services.sleep login    log in once, keeps only the session in xiaomi_session.json
#   python -m app.services.sleep code N   the code xiaomi sent when it asks to confirm a new device
#   python -m app.services.sleep dump N   print the raw daily reports of the last N days
#   python -m app.services.sleep sync N   pull N days into sleep_cache.json
import base64
import hashlib
import http.cookiejar
import json
import os
import secrets
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from app import env
from app.paths import ROOT

SESSION = ROOT / "xiaomi_session.json"
CACHE = ROOT / "sleep_cache.json"

ACCOUNT = "https://account.xiaomi.com"
SID = "miothealth"
REGIONS = ["de", "sg", "ru", "us", "i2", "cn"]  # mi fitness keeps data on the shard of the account region
UA = "Android-3.60.0"
KEEP_DAYS = 30

TEMPLATE = f"""
# Xiaomi account for the smart band (Mi Fitness). Replace {env.PLACEHOLDER}, no quotes.
XIAOMI_USER={env.PLACEHOLDER}
XIAOMI_PASSWORD={env.PLACEHOLDER}
"""

_busy = threading.Lock()


class LoginError(Exception):
    pass


class VerifyNeeded(LoginError):
    pass


def configured() -> bool:
    return SESSION.exists() or env.has("XIAOMI_USER", "XIAOMI_PASSWORD")


def _session() -> dict:
    try:
        return json.loads(SESSION.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_session(s: dict) -> None:
    SESSION.write_text(json.dumps(s, indent=2), encoding="utf-8")


def _json(raw: bytes) -> dict:
    return json.loads(raw.decode().removeprefix("&&&START&&&"))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def _opener(s: dict, extra: list | None = None) -> tuple:
    jar = http.cookiejar.CookieJar()
    cookies = [(n, s[n], "account.xiaomi.com") for n in ("deviceId", "userId", "passToken") if s.get(n)]
    for name, value, domain in cookies + (extra or []):
        jar.set_cookie(http.cookiejar.Cookie(
            0, name, str(value), None, False, domain, False, domain.startswith("."),
            "/", True, False, None, False, None, None, {},
        ))  # fmt: skip
    cookie_handler = urllib.request.HTTPCookieProcessor(jar)
    return urllib.request.build_opener(cookie_handler), urllib.request.build_opener(cookie_handler, _NoRedirect), jar


def _open(opener, url: str, data: dict | None = None) -> bytes:
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers={"User-Agent": UA})
    with opener.open(req, timeout=20) as r:
        return r.read()


def _hop(opener, url: str):
    # one step of a redirect chain: the headers matter, not the body
    try:
        with opener.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20) as r:
            return r.headers
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307):
            return e.headers
        raise


def _finish(s: dict, opener, jar, user_id, ssecurity: str, nonce, location: str) -> dict:
    # the sts callback sets serviceToken for the health api
    sign = base64.b64encode(hashlib.sha1(f"nonce={nonce}&{ssecurity}".encode()).digest()).decode()
    _open(opener, location + "&clientSign=" + urllib.parse.quote(sign))
    cookies = {c.name: c.value for c in jar}
    if not cookies.get("serviceToken"):
        raise LoginError("no serviceToken after login")
    s.update(
        userId=str(user_id or cookies.get("userId", "")),
        cUserId=cookies.get("cUserId") or s.get("cUserId", ""),
        ssecurity=ssecurity,
        serviceToken=cookies["serviceToken"],
        passToken=cookies.get("passToken") or s.get("passToken"),
        at=int(time.time()),
    )
    s.pop("pending", None)
    _save_session(s)
    return s


def _refresh(s: dict) -> dict | None:
    # passToken lives for months: a new serviceToken without the password
    if not s.get("passToken"):
        return None
    opener, _, jar = _opener(s)
    info = _json(_open(opener, f"{ACCOUNT}/pass/serviceLogin?sid={SID}&_json=true"))
    if info.get("code") != 0 or not info.get("ssecurity"):
        return None
    return _finish(s, opener, jar, info["userId"], info["ssecurity"], info["nonce"], info["location"])


def login() -> dict:
    e = env.read()
    if not env.has("XIAOMI_USER", "XIAOMI_PASSWORD"):
        env.add_template(TEMPLATE)
        raise LoginError("fill XIAOMI_USER and XIAOMI_PASSWORD in .env")
    s = _session()
    s.setdefault("deviceId", secrets.token_hex(8))
    opener, _, jar = _opener(s)
    first = _json(_open(opener, f"{ACCOUNT}/pass/serviceLogin?sid={SID}&_json=true"))
    form = {
        "_json": "true",
        "qs": first.get("qs", ""),
        "sid": SID,
        "_sign": first.get("_sign", ""),
        "callback": first.get("callback", ""),
        "user": e["XIAOMI_USER"],
        "hash": hashlib.md5(e["XIAOMI_PASSWORD"].encode()).hexdigest().upper(),
    }
    info = _json(_open(opener, f"{ACCOUNT}/pass/serviceLoginAuth2", form))
    if info.get("notificationUrl") and not info.get("ssecurity"):
        _save_session(s)  # keep the deviceId: after the check xiaomi trusts this "phone"
        raise VerifyNeeded(info["notificationUrl"])
    if info.get("code") != 0 or not info.get("location"):
        raise LoginError(info.get("desc") or f"code {info.get('code')}")
    return _finish(s, opener, jar, info["userId"], info["ssecurity"], info["nonce"], info["location"])


def send_code(notification_url: str) -> str:
    # xiaomi asks to confirm a new device: a code goes to the email (or phone) of the account
    s = _session()
    opener, _, jar = _opener(s)
    context = urllib.parse.parse_qs(urllib.parse.urlparse(notification_url).query)["context"][0]
    query = urllib.parse.urlencode({"sid": SID, "context": context, "_locale": "en_US"})
    _open(opener, notification_url)
    options = _json(_open(opener, f"{ACCOUNT}/identity/list?{query}"))
    flags = options.get("options") or [options.get("flag", 8)]
    flag = 8 if 8 in flags else flags[0]
    kind = "Email" if flag == 8 else "Phone"
    ick = next((c.value for c in jar if c.name == "ick"), "")
    url = f"{ACCOUNT}/identity/auth/send{kind}Ticket?{query}&_dc={int(time.time() * 1000)}"
    res = _json(_open(opener, url, {"retry": "0", "icode": "", "_json": "true", "ick": ick}))
    if res.get("code") != 0:
        raise LoginError(f"code not sent: {res.get('desc') or res.get('code')}")
    s["pending"] = {"context": context, "flag": flag, "cookies": [[c.name, c.value, c.domain] for c in jar]}
    _save_session(s)
    return "email" if flag == 8 else "phone"


def verify(code: str) -> dict:
    s = _session()
    pending = s.get("pending")
    if not pending:
        raise LoginError("no code was requested, run login first")
    opener, plain, jar = _opener(s, pending["cookies"])
    flag = pending["flag"]
    kind = "Email" if flag == 8 else "Phone"
    query = urllib.parse.urlencode({"_flag": flag, "sid": SID, "context": pending["context"], "_json": "true"})
    form = {"_flag": flag, "ticket": code.strip(), "trust": "true", "_json": "true"}
    res = _json(_open(opener, f"{ACCOUNT}/identity/auth/verify{kind}?{query}", form))
    if res.get("code") != 0 or not res.get("location"):
        raise LoginError(res.get("desc") or f"code {res.get('code')}")
    # the chain ends at serviceLoginAuth2/end: ssecurity in Extension-Pragma, the sts url in Location
    url, extra, sts = res["location"], {}, None
    for _ in range(6):
        headers = _hop(plain, url)
        extra = json.loads(headers.get("Extension-Pragma") or "{}")
        sts = headers.get("Location")
        if extra.get("ssecurity") or not sts:
            break
        url = sts
    if not extra.get("ssecurity") or not sts:
        raise LoginError("no ssecurity after the check")
    nonce = extra.get("nonce") or urllib.parse.parse_qs(urllib.parse.urlparse(sts).query).get("nonce", [""])[0]
    return _finish(s, opener, jar, None, extra["ssecurity"], nonce, sts)


class _Rc4:
    # rc4 with the first 1024 bytes of the stream dropped
    def __init__(self, key: bytes):
        box, j = list(range(256)), 0
        for i in range(256):
            j = (j + box[i] + key[i % len(key)]) & 0xFF
            box[i], box[j] = box[j], box[i]
        self.box, self.i, self.j = box, 0, 0
        self.crypt(bytes(1024))

    def crypt(self, data: bytes) -> bytes:
        box, i, j, out = self.box, self.i, self.j, bytearray()
        for b in data:
            i = (i + 1) & 0xFF
            j = (j + box[i]) & 0xFF
            box[i], box[j] = box[j], box[i]
            out.append(b ^ box[(box[i] + box[j]) & 0xFF])
        self.i, self.j = i, j
        return bytes(out)


def _sign(path: str, params: dict, nonce: str) -> str:
    text = "&".join(["POST", path, *(f"{k}={params[k]}" for k in sorted(params)), nonce])
    return base64.b64encode(hashlib.sha1(text.encode()).digest()).decode()


def _post(s: dict, region: str, path: str, data: dict) -> dict:
    nonce = base64.b64encode(os.urandom(8) + struct.pack(">i", int(time.time() // 60))).decode()
    signed = base64.b64encode(hashlib.sha256(base64.b64decode(s["ssecurity"]) + base64.b64decode(nonce)).digest())
    key = base64.b64decode(signed)
    params = {"data": json.dumps(data, separators=(",", ":"))}
    params["rc4_hash__"] = _sign(path, params, signed.decode())
    rc4 = _Rc4(key)
    enc = {k: base64.b64encode(rc4.crypt(params[k].encode())).decode() for k in sorted(params)}
    enc["signature"] = _sign(path, enc, signed.decode())
    enc["_nonce"] = nonce
    host = "hlth.io.mi.com" if region == "cn" else f"{region}.hlth.io.mi.com"
    req = urllib.request.Request(
        f"https://{host}{path}",
        data=urllib.parse.urlencode(enc).encode(),
        headers={
            "User-Agent": UA,
            "X-XIAOMI-PROTOCAL-FLAG-CLI": "PROTOCAL-HTTP2",
            "Cookie": f"cUserId={s['cUserId']}; serviceToken={s['serviceToken']}; locale=en_US",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        res = json.loads(_Rc4(key).crypt(base64.b64decode(r.read())))
    if res.get("code") != 0:
        raise RuntimeError(f"{path}: {res.get('code')} {res.get('message', '')}")
    return res.get("result") or {}


def _call(path: str, data: dict) -> dict:
    s = _session()
    if not s.get("serviceToken"):
        s = login()
    for attempt in range(2):
        try:
            return _post(s, s.get("region", "de"), path, data)
        except urllib.error.HTTPError as e:
            if e.code != 401 or attempt:
                raise
            s = _refresh(s) or login()
    raise RuntimeError("unreachable")


def _daily(key: str, start: int, end: int) -> list:
    data = {"tag": "daily_report", "key": key, "start_time": start, "end_time": end, "reverse": True, "limit": 100}
    return _call("/app/v1/data/get_aggregated_fitness_data_by_time", data).get("data_list") or []


def find_region() -> str:
    # the account lives on one shard, the others answer with nothing
    s = _session()
    now = int(time.time())
    for region in REGIONS:
        s["region"] = region
        _save_session(s)
        try:
            if _daily("sleep", now - KEEP_DAYS * 86400, now) or _daily("steps", now - 7 * 86400, now):
                return region
        except Exception:
            continue
    s["region"] = REGIONS[0]
    _save_session(s)
    raise RuntimeError("no data on any region, is the band synced to mi fitness?")


def _segment(g: dict) -> dict:
    # times are utc, "timezone" is in quarters of an hour
    tz = g.get("timezone", 8) * 900
    return {
        "start": g["bedtime"],
        "end": g["wake_up_time"],
        "tz": tz,
        "dur": g.get("duration", 0),
        "deep": g.get("sleep_deep_duration", 0),
        "light": g.get("sleep_light_duration", 0),
        "rem": g.get("sleep_rem_duration", 0),
        "awake": g.get("sleep_awake_duration", 0),
        "wakes": g.get("awake_count", 0),
        "hr": g.get("avg_hr"),
        "hr_min": g.get("min_hr"),
        "hr_max": g.get("max_hr"),
        "breath": g.get("avg_breath"),
    }


def _day(item: dict) -> dict | None:
    v = json.loads(item.get("value") or "{}")
    if item.get("deleted") or not v.get("total_duration"):
        return None
    segments = sorted((_segment(g) for g in v.get("segment_details") or []), key=lambda g: g["start"])
    return {
        "day": time.strftime("%Y-%m-%d", time.gmtime(item["time"])),  # the day of waking up
        "score": v.get("sleep_score"),
        "total": v["total_duration"],
        "deep": v.get("sleep_deep_duration", 0),
        "light": v.get("sleep_light_duration", 0),
        "rem": v.get("sleep_rem_duration", 0),
        "awake": v.get("sleep_awake_duration", 0),
        "hr": v.get("avg_hr"),
        "segments": segments,
    }


def _activity(days: int) -> tuple[dict, dict | None]:
    # steps, distance, calories and heart rate by day; the last measured pulse
    now = int(time.time())
    out, latest = {}, None
    for item in _daily("steps", now - days * 86400, now + 86400):
        v = json.loads(item["value"])
        day = time.strftime("%Y-%m-%d", time.gmtime(item["time"]))
        out[day] = {
            "steps": v.get("steps", 0),
            "km": round(v.get("distance", 0) / 1000, 1),
            "kcal": v.get("calories", 0),
        }
    for item in _daily("heart_rate", now - days * 86400, now + 86400):
        v = json.loads(item["value"])
        day = time.strftime("%Y-%m-%d", time.gmtime(item["time"]))
        out.setdefault(day, {"steps": 0, "km": 0, "kcal": 0}).update(rhr=v.get("avg_rhr"), hr=v.get("avg_hr"))
        last = v.get("latest_hr") or {}
        # "time" is shifted by the zone, "dbTime" is the real utc one
        ts = last.get("dbTime") or last.get("time")
        if ts and (not latest or ts > latest["time"]):
            latest = {"bpm": last["bpm"], "time": ts}
    return out, latest


def sync(days: int = 7) -> dict:
    now = int(time.time())
    fresh = {d["day"]: d for d in filter(None, map(_day, _daily("sleep", now - days * 86400, now + 86400)))}
    old = cached() or {"days": []}
    merged = {d["day"]: d for d in old["days"]} | fresh
    keep = sorted(merged.values(), key=lambda d: d["day"])[-KEEP_DAYS * 2 :]
    activity, latest = _activity(days if old.get("activity") else KEEP_DAYS)
    activity = dict(sorted((old.get("activity", {}) | activity).items())[-KEEP_DAYS * 2 :])
    data = {"ts": time.time(), "days": keep, "activity": activity, "pulse": latest or old.get("pulse")}
    CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def cached() -> dict | None:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return None


def age() -> float:
    return time.time() - CACHE.stat().st_mtime if CACHE.exists() else 1e9


def is_syncing() -> bool:
    return _busy.locked()


def sync_in_background(done, days: int = 7) -> bool:
    if not _busy.acquire(blocking=False):
        return False

    def run():
        try:
            data, err = sync(days), None
        except Exception as e:
            data, err = None, e
        finally:
            _busy.release()
        done(data, err)

    threading.Thread(target=run, daemon=True, name="sleep").start()
    return True


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dump"
    if cmd == "login":
        try:
            login()
            print("ok, region:", find_region())
        except VerifyNeeded as e:
            print(f"xiaomi sent a code to the {send_code(str(e))}, run: python -m app.services.sleep code N")
        except LoginError as e:
            print("login failed:", e)
    elif cmd == "code":
        verify(sys.argv[2])
        print("ok, region:", find_region())
    elif cmd == "dump":
        now = int(time.time())
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 3
        print(json.dumps(_daily("sleep", now - days * 86400, now + 86400), ensure_ascii=False, indent=1))
    elif cmd == "sync":
        print(json.dumps(sync(int(sys.argv[2]) if len(sys.argv) > 2 else 7), ensure_ascii=False, indent=1))
