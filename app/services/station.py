# oukitel power station through the acceleronix cloud, the same one the app uses
# protocol from github.com/VictorCV-DAM/ha-oukitel, login from .env (OUKITEL_EMAIL / OUKITEL_PASSWORD)

import hashlib
import json
import random
import string
import threading
import time
import urllib.parse
import urllib.request
from base64 import b64encode

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from app.env import ENV, PLACEHOLDER
from app.env import read as read_env
from app.paths import ROOT
from app.services import powerlog

STATE = ROOT / "station_state.json"

CAPACITY_WH = 1024  # P1000E Plus
POLL_SEC = 60
WAKE_SEC = 45  # firmware drops fast reporting after ~90 s, without it the cloud returns old data
GRID_W = 5  # more input than this means power is on
LOW_LEVELS = (30, 15)  # warnings while the station supplies something
CHARGE_HINT_PCT = 50  # below this remind to charge while there is power
IDLE_OFF_MIN = 10  # ac on with no load this long: switch it off, the inverter eats ~15 W
HOT_C, COOL_C = 55, 50  # temp at idle already reads ~40, calibrate from power.db
OFFLINE_ALERT_MIN = 10  # in ups mode the station should never go offline
GRID_CONFIRM = 2  # polls in a row before we believe power went or came back
CONFIRM_SEC = 15  # poll sooner while a grid change waits for confirmation

SERVERS = {
    "EU": "https://iot-api.acceleronix.io",
    "US": "https://iot-api.quectelus.com",
    "CN": "https://iot-api.quectelcn.com",
}
USER_DOMAIN = "E.SP.4294967410"
USER_DOMAIN_SECRET = "3aRNUwWahjyANa7WfBK2wCCkxCexB6nXxKJwXxfePvzf"
APP_ID, APP_VER = "277", "3.7.5"


def ensure_env_template():
    if not ENV.exists():
        ENV.write_text(
            "# Логін і пароль від апки Wonderfree (Oukitel). Заміни ВСТАВ_СЮДИ, лапки не потрібні.\n"
            f"OUKITEL_EMAIL={PLACEHOLDER}\n"
            f"OUKITEL_PASSWORD={PLACEHOLDER}\n"
            "# Регіон сервера: EU (за замовчуванням), US або CN\n"
            "OUKITEL_REGION=EU\n",
            encoding="utf-8",
        )


def configured():
    e = read_env()
    return all(e.get(k) and e.get(k) != PLACEHOLDER for k in ("OUKITEL_EMAIL", "OUKITEL_PASSWORD"))


class CloudError(Exception):
    pass


class Cloud:
    def __init__(self, email, password, region="EU"):
        self.email, self._pwd = email, password
        self.base = SERVERS.get(region.upper(), SERVERS["EU"])
        self.token, self.expiry = None, 0
        self.dk = self.pk = None
        self.name = None
        self.online = True

    def _req(self, method, path, params=None, form=None, js=None, auth=True):
        headers = {"appversion": APP_VER, "appsystemtype": "android", "appid": APP_ID, "User-Agent": "okhttp/4.9.3"}
        if auth:
            headers["Authorization"] = self.token
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif js is not None:
            data = json.dumps(js).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())

    def _call(self, method, path, **kw):
        self.ensure_login()
        res = self._req(method, path, **kw)
        if res.get("code") == 5032:  # token expired
            self.token = None
            self.ensure_login()
            res = self._req(method, path, **kw)
        return res

    def login(self):
        rnd = "".join(random.choice(string.ascii_letters + string.digits) for _ in range(16))
        md5 = hashlib.md5(rnd.encode()).hexdigest().upper()
        cipher = AES.new(md5[8:24].encode(), AES.MODE_CBC, (md5[16:24] + md5[8:16]).encode())
        pwd = b64encode(cipher.encrypt(pad(self._pwd.encode(), 16))).decode()
        sig = hashlib.sha256((self.email + pwd + rnd + USER_DOMAIN_SECRET).encode()).hexdigest()
        res = self._req(
            "POST",
            "/v2/enduser/enduserapi/emailPwdLogin",
            auth=False,
            form={"email": self.email, "pwd": pwd, "random": rnd, "userDomain": USER_DOMAIN, "signature": sig},
        )
        if res.get("code") != 200:
            raise CloudError(f"логін не пройшов: {res.get('msg')} (код {res.get('code')})")
        d = res["data"]
        self.token = d["accessToken"]["token"]
        exp = d["accessToken"].get("expirationTime") or time.time() + 7000
        self.expiry = exp / 1000 if exp > 1e11 else exp

    def ensure_login(self):
        if not self.token or time.time() > self.expiry - 120:
            self.login()

    def find_device(self):
        res = self._call("GET", "/v2/binding/enduserapi/userDeviceList")
        devs = (res.get("data") or {}).get("list") or []
        if not devs:
            raise CloudError("в акаунті немає станцій")
        d = devs[0]
        self.dk, self.pk = d["deviceKey"], d["productKey"]
        self.name = d.get("deviceName") or d.get("productName") or "Oukitel"
        self.online = _truthy(d, ("onlineStatus", "isOnline", "deviceStatus", "online", "status"), True)
        return d

    def wake(self):
        # ask the station to report more often, like the app does
        if not self.dk:
            self.find_device()
        self._call(
            "POST",
            "/v2/binding/enduserapi/batchControlDevice",
            js={
                "data": json.dumps([{"high_frequency_reporting": 3}]),
                "deviceList": [{"deviceKey": self.dk, "productKey": self.pk}],
                "cacheTime": 60,
                "isCache": 1,
                "isCover": 1,
                "dataFormat": 0,
                "type": 2,
            },
        )

    def control(self, values, what):
        # write properties of the device model, e.g. {"ac_switch": True} or {"led_status": 1}
        if not self.dk:
            self.find_device()
        res = self._call(
            "POST",
            "/v2/binding/enduserapi/batchControlDevice",
            js={
                "data": json.dumps([values]),
                "deviceList": [{"deviceKey": self.dk, "productKey": self.pk}],
                "cacheTime": 60,
                "isCache": 1,
                "isCover": 1,
                "dataFormat": 0,
                "type": 2,
            },
        )
        if res.get("code") != 200 or (res.get("data") or {}).get("failureList"):
            raise CloudError(f"{what} не перемкнувся: {res.get('msg')} (код {res.get('code')})")

    def set_ac(self, on):
        self.control({"ac_switch": bool(on)}, "AC")

    def telemetry(self):
        if not self.dk:
            self.find_device()
        res = self._call(
            "GET", "/v2/binding/enduserapi/getDeviceBusinessAttributes", params={"pk": self.pk, "dk": self.dk}
        )
        if res.get("code") != 200:
            raise CloudError(f"телеметрія: {res.get('msg')} (код {res.get('code')})")
        data = res.get("data") or {}
        m = {"online": _truthy(data.get("deviceData") or {}, ("isOnline", "onlineStatus", "online"), self.online)}
        for it in data.get("customizeTslInfo") or []:
            code, raw, typ = it.get("resourceCode"), it.get("resourceValce"), it.get("dataType")
            if not code or raw is None:
                continue
            try:
                if typ == "INT":
                    m[code] = int(raw)
                elif typ in ("FLOAT", "DOUBLE"):
                    m[code] = float(raw)
                elif typ == "BOOL":
                    m[code] = str(raw).lower() == "true"
                elif typ == "STRUCT":
                    m[code] = json.loads(raw)
                else:
                    m[code] = raw
            except Exception:
                m[code] = raw
        return m


def _truthy(d, keys, default):
    for k in keys:
        v = d.get(k)
        if v is None:
            continue
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return int(v) == 1
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "online")
    return default


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def normalize(raw):
    pct = int(_num(raw.get("battery_percentage")))
    ac_in = _num(raw.get("ac_input"))
    dc_in = _num(raw.get("dc_input"))
    tin = max(_num(raw.get("total_input_power")), ac_in + dc_in)
    tout = _num(raw.get("total_output_power"))
    net = tin - tout
    mins = None
    if net < -5 and pct > 0:
        mins = int(raw.get("remain_time") or 0)
        if not mins or mins >= 5940:
            mins = int(pct / 100 * CAPACITY_WH / -net * 60)
    charge_mins = None
    if net > 5 and pct < 100:
        charge_mins = int(raw.get("remain_charging_time") or 0)
        if not charge_mins or charge_mins >= 5940:
            charge_mins = int((100 - pct) / 100 * CAPACITY_WH / net * 60)
    return {
        "ts": time.time(),
        "online": bool(raw.get("online", True)),
        "pct": pct,
        "ac_in": round(ac_in),
        "dc_in": round(dc_in),
        "in_w": round(tin),
        "out_w": round(tout),
        "mins_left": mins,
        "charge_mins": charge_mins,
        "ac_on": bool(raw.get("ac_switch")),
        "led": int(_num(raw.get("led_status"))),
        "temp": raw.get("temp"),
        "inv_temp": raw.get("inverter_temp"),
        "fault": raw.get("device_fault_status"),
    }


def mode_of(s):
    # charging: plugged in, supply: runs things from the battery, idle: nothing
    if s["ac_in"] >= GRID_W or s["in_w"] >= GRID_W:
        return "charging"
    return "supply" if s["out_w"] >= GRID_W else "idle"


def fmt_mins(m):
    if m is None:
        return "—"
    h, mm = divmod(int(m), 60)
    return f"{h} год {mm:02d} хв" if h else f"{mm} хв"


def status_text(s):
    if not s:
        return "🔌 Станція: ще немає даних."
    ago = int(time.time() - s["ts"])
    age = f"{ago} с тому" if ago < 120 else f"{ago // 60} хв тому"
    if not s.get("online"):
        return f"🔌 Станція вимкнена або без Wi-Fi. Останні дані: {s['pct']}%, {age}."
    lines = [
        f"🔋 Заряд: {s['pct']}%",
        {
            "charging": "🔌 Заряджається від розетки",
            "supply": "⚡ Живить пристрої від батареї",
            "idle": "💤 Простоює (нічого не підключено)",
        }[mode_of(s)],
        f"⬇️ Вхід: {s['in_w']} W" + (f" (мережа {s['ac_in']} W)" if s["dc_in"] else ""),
        f"⬆️ Вихід: {s['out_w']} W",
    ]
    if s.get("mins_left"):
        lines.append(f"⏳ Вистачить ≈ {fmt_mins(s['mins_left'])}")
    elif s.get("charge_mins"):
        lines.append(f"⏳ До 100% ≈ {fmt_mins(s['charge_mins'])}")
    lines.append(f"🔌 Розетка AC: {'увімкнена' if s.get('ac_on') else 'вимкнена'}")
    lines.append(f"🕒 Дані: {age}")
    return "\n".join(lines)


def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(st):
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


class Monitor:
    # background thread: polls the station and queues events for the bot

    def __init__(self, log=print):
        self.log = log
        self.events = []  # (kind, snapshot)
        self.lock = threading.Lock()
        self.cloud = None
        st = load_state()
        self.last = st.get("last")
        self.flags = set(st.get("flags", []))  # what was already sent: low:30, low:15, full, ...
        self._last_wake = 0
        self.error = None
        self.idle_since = None
        self.hold_ac_until = 0  # do not switch ac off before this
        self.ups = False  # station always plugged in, router and laptop run through it
        self.grid = None  # is there power, seen by the station in ups mode
        self._grid_votes = 0
        self.outage_start = st.get("outage_start")  # (ts, pct) when power went off
        self.offline_since = None
        self.fault_base = None
        self._lock_cloud = threading.Lock()

    def start(self):
        ensure_env_template()
        threading.Thread(target=self._run, daemon=True, name="station").start()

    def pop_events(self):
        with self.lock:
            ev, self.events = self.events, []
        return ev

    def poll_once(self):
        if not configured():
            self.error = "не налаштовано"
            return None
        env = read_env()
        if self.cloud is None or self.cloud.email != env["OUKITEL_EMAIL"]:
            self.cloud = Cloud(env["OUKITEL_EMAIL"], env["OUKITEL_PASSWORD"], env.get("OUKITEL_REGION", "EU"))
        if time.time() - self._last_wake > WAKE_SEC:
            try:
                self.cloud.wake()
            except Exception as e:
                self.log(f"станція: wake {e!r}")
            self._last_wake = time.time()
        with self._lock_cloud:
            self.cloud.find_device()  # updates online / offline, telemetry alone returns stale data
            s = normalize(self.cloud.telemetry())
        self.last, self.error = s, None
        self._track(s)
        self._auto_ac(s)
        self._history(s)
        save_state({"last": s, "flags": sorted(self.flags), "outage_start": self.outage_start})
        return s

    def fresh(self, max_age=30):
        if self.last and time.time() - self.last["ts"] < max_age:
            return self.last
        try:
            return self.poll_once() or self.last
        except Exception as e:
            self.log(f"станція: {e!r}")
            return self.last

    def set_ac(self, on, why=""):
        # True if the state changed
        if not configured():
            return False
        if self.cloud is None:
            env = read_env()
            self.cloud = Cloud(env["OUKITEL_EMAIL"], env["OUKITEL_PASSWORD"], env.get("OUKITEL_REGION", "EU"))
        with self._lock_cloud:
            self.cloud.find_device()
            if not self.cloud.online:
                if self.last:
                    self.last["online"] = False
                raise CloudError("станція не в мережі — вимкнена або заснула. Увімкни її кнопкою.")
            if self.last and self.last.get("online") and self.last.get("ac_on") == bool(on):
                return False
            self.cloud.set_ac(on)
        if self.last:
            self.last["ac_on"] = bool(on)
        self.idle_since = None
        self.log(f"станція: AC {'ON' if on else 'OFF'} {why}")
        powerlog.event("ac_on" if on else "ac_off", why.strip("()"))
        return True

    def set_led(self, mode):
        # station light: 0 off, 1 on, 2 flashing
        if not configured():
            return
        if self.cloud is None:
            env = read_env()
            self.cloud = Cloud(env["OUKITEL_EMAIL"], env["OUKITEL_PASSWORD"], env.get("OUKITEL_REGION", "EU"))
        with self._lock_cloud:
            self.cloud.find_device()
            if not self.cloud.online:
                raise CloudError("станція не в мережі — вимкнена або заснула.")
            self.cloud.control({"led_status": int(mode)}, "Ліхтар")
        if self.last:
            self.last["led"] = int(mode)

    def _auto_ac(self, s):
        if (
            self.ups
            or not s["online"]
            or not s.get("ac_on")
            or mode_of(s) != "idle"
            or time.time() < self.hold_ac_until
        ):
            self.idle_since = None
            return
        self.idle_since = self.idle_since or time.time()
        if time.time() - self.idle_since >= IDLE_OFF_MIN * 60:
            try:
                self.set_ac(False, "(простій)")
            except Exception as e:
                self.log(f"станція: {e!r}")

    def _history(self, s):
        # events in power.db when the mode or the connection changes
        key = (mode_of(s), s["online"])
        prev = getattr(self, "_hist_key", None)
        self._hist_key = key
        if prev is None or prev == key:
            return
        if prev[1] != s["online"]:
            powerlog.event("station_online" if s["online"] else "station_offline", f"{s['pct']}%")
        if s["online"] and prev[0] != key[0]:
            powerlog.event(f"station_{key[0]}", f"{s['pct']}% in {s['in_w']} W out {s['out_w']} W")

    def _emit(self, kind, s):
        with self.lock:
            self.events.append((kind, s))

    def _once(self, flag, s):
        if flag not in self.flags:
            self.flags.add(flag)
            self._emit(flag, s)

    def _track(self, s):
        if not s["online"]:
            self.offline_since = self.offline_since or time.time()
            if self.ups and time.time() - self.offline_since >= OFFLINE_ALERT_MIN * 60:
                self._once("offline", s)
            return
        self.offline_since = None
        self.flags.discard("offline")
        self._track_health(s)
        if self.ups:
            self._track_grid(s)
        pct, mode = s["pct"], mode_of(s)
        if mode == "supply":
            for lvl in sorted(LOW_LEVELS):  # lowest level first
                if pct <= lvl:
                    self.flags.update(f"low:{level}" for level in LOW_LEVELS if level > lvl)
                    self._once(f"low:{lvl}", s)
                    break
        if mode == "charging":
            self.flags -= {f"low:{level}" for level in LOW_LEVELS}
            if pct >= 100 and not self.ups:
                self._once("full", s)
        if pct < 95:
            self.flags.discard("full")
        if pct >= 90:
            self.flags.discard("charge_hint")
        if mode == "idle" and pct <= CHARGE_HINT_PCT and not self.ups:
            self._once("charge_hint", s)

    def _track_health(self, s):
        temp = max(_num(s.get("temp")), _num(s.get("inv_temp")))
        if temp >= HOT_C:
            self._once("hot", s)
        elif temp < COOL_C:
            self.flags.discard("hot")
        if self.ups and not s.get("ac_on"):
            self._once("ac_off_ups", s)
        else:
            self.flags.discard("ac_off_ups")
        fault = s.get("fault")
        if fault is None:
            return
        if self.fault_base is None:
            self.fault_base = fault  # what "normal" looks like for this firmware
        elif fault != self.fault_base and f"fault:{fault}" not in self.flags:
            powerlog.event("station_fault", str(fault))
            self._once(f"fault:{fault}", s)

    def _track_grid(self, s):
        # in ups mode the station sees the grid first: no input and it supplies the load
        grid = not (s["ac_in"] < GRID_W and s["in_w"] < GRID_W and s["out_w"] >= GRID_W)
        if self.grid is None:
            self.grid = grid
            return
        if grid == self.grid:
            self._grid_votes = 0
            return
        self._grid_votes += 1
        if self._grid_votes < GRID_CONFIRM:
            return
        self.grid, self._grid_votes = grid, 0
        if grid:
            powerlog.event("grid_back", f"{s['pct']}%")
            self._emit("grid_back", dict(s, outage_start=self.outage_start))
            self.outage_start = None
        else:
            self.outage_start = (time.time() - POLL_SEC - CONFIRM_SEC, s["pct"])
            powerlog.event("grid_lost", f"{s['pct']}%")
            self._emit("grid_lost", s)

    def _run(self):
        while True:
            try:
                self.poll_once()
            except Exception as e:
                msg = str(e)
                if self.error != msg:
                    self.log(f"станція: {msg}")
                self.error = msg
                if "логін" in msg:
                    self.cloud = None
                    time.sleep(600)  # do not hammer the server with a wrong password
            time.sleep(CONFIRM_SEC if self._grid_votes else POLL_SEC)


if __name__ == "__main__":
    ensure_env_template()
    if not configured():
        print(f"Впиши логін/пароль у {ENV}")
    else:
        m = Monitor()
        s = m.poll_once()
        print(m.cloud.name)
        print(status_text(s))
