# xiaomi lamp over the local network (miio protocol, no cloud)
# config.json: "lamp": {"ip": "...", "token": "<32 hex>"}, cli: python -m app.services.lamp on|off|toggle|info

import hashlib
import json
import os
import socket
import sys
import time

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.paths import CONFIG

PORT = 54321


def _md5(b):
    return hashlib.md5(b).digest()


class MiIO:
    def __init__(self, ip, token):
        self.ip = ip
        self.token = bytes.fromhex(token)
        self.key = _md5(self.token)
        self.iv = _md5(self.key + self.token)
        self.did = None
        self.stamp = 0
        self.stamp_at = 0.0
        self.msg_id = int(time.time()) % 10000

    def _crypt(self, data, enc):
        c = Cipher(algorithms.AES(self.key), modes.CBC(self.iv))
        if enc:
            p = padding.PKCS7(128).padder()
            data = p.update(data) + p.finalize()
            e = c.encryptor()
            return e.update(data) + e.finalize()
        d = c.decryptor()
        out = d.update(data) + d.finalize()
        u = padding.PKCS7(128).unpadder()
        return u.update(out) + u.finalize()

    def _udp(self, packet, timeout=3):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        try:
            s.sendto(packet, (self.ip, PORT))
            return s.recvfrom(4096)[0]
        finally:
            s.close()

    def _hello(self):
        r = self._udp(bytes.fromhex("21310020" + "ff" * 28))
        self.did = r[8:12]
        self.stamp = int.from_bytes(r[12:16], "big")
        self.stamp_at = time.time()

    def call(self, method, params=None, retries=2):
        last = None
        for _ in range(retries + 1):
            try:
                if self.did is None or time.time() - self.stamp_at > 60:
                    self._hello()
                self.msg_id += 1
                body = json.dumps({"id": self.msg_id, "method": method, "params": params or []}).encode()
                payload = self._crypt(body, True)
                stamp = self.stamp + int(time.time() - self.stamp_at) + 1
                head = (
                    b"\x21\x31"
                    + (32 + len(payload)).to_bytes(2, "big")
                    + b"\x00" * 4
                    + self.did
                    + stamp.to_bytes(4, "big")
                )
                packet = head + _md5(head + self.token + payload) + payload
                r = self._udp(packet)
                data = json.loads(self._crypt(r[32:], False).rstrip(b"\x00").decode())
                if "error" in data:
                    raise RuntimeError(data["error"])
                return data.get("result")
            except (socket.timeout, OSError) as e:
                last = e
                self.did = None
        raise RuntimeError(f"лампа не відповідає ({last!r})")


class Lamp:
    # miot models: siid 2 is the light (piid 1 on, 2 brightness, 3 color temperature)

    def __init__(self, ip, token):
        self.dev = MiIO(ip, token)
        self._model = None

    @property
    def model(self):
        if self._model is None:
            self._model = (self.dev.call("miIO.info") or {}).get("model", "?")
        return self._model

    @property
    def legacy(self):  # old yeelink / philips models have other commands
        return self.model.startswith(("yeelink.", "philips."))

    def _set(self, piid, value):
        return self.dev.call("set_properties", [{"did": "x", "siid": 2, "piid": piid, "value": value}])

    def state(self):
        if self.legacy:
            on, br, ct, rgb, mode = self.dev.call("get_prop", ["power", "bright", "ct", "rgb", "color_mode"])
            return {
                "on": on == "on",
                "bright": int(br),
                "ct": int(ct or 0),
                "rgb": int(rgb or 0) if str(mode) == "1" else None,
            }
        res = self.dev.call("get_properties", [{"did": "x", "siid": 2, "piid": p} for p in (1, 2, 3)])
        v = {r["piid"]: r.get("value") for r in res if r.get("code", 0) == 0}
        return {"on": bool(v.get(1)), "bright": v.get(2), "ct": v.get(3)}

    def power(self, on):
        if self.legacy:
            return self.dev.call("set_power", ["on" if on else "off", "smooth", 500])  # smooth, no flash
        return self._set(1, bool(on))

    def toggle(self):
        on = not self.state()["on"]
        self.power(on)
        return on

    def bright(self, pct):
        pct = max(1, min(100, int(pct)))
        if self.legacy:
            return self.dev.call("set_bright", [pct, "smooth", 400])
        return self._set(2, pct)

    def ct(self, kelvin):
        kelvin = max(1700, min(6500, int(kelvin)))
        if self.legacy:
            return self.dev.call("set_ct_abx", [kelvin, "smooth", 300])
        return self._set(3, kelvin)

    def rgb(self, color):  # color: 0xRRGGBB
        return self.dev.call("set_rgb", [int(color), "smooth", 300])


class FakeLamp(Lamp):
    # for tests, so the real lamp does not blink: LAMP_DRY=1

    def __init__(self):
        self._model = "fake"
        self.s = {"on": False, "bright": 50, "ct": 4000, "rgb": None}

    legacy = True

    def state(self):
        return dict(self.s)

    def power(self, on):
        self.s["on"] = bool(on)

    def bright(self, pct):
        self.s["bright"] = max(1, min(100, int(pct)))

    def ct(self, kelvin):
        self.s.update(ct=int(kelvin), rgb=None)

    def rgb(self, color):
        self.s["rgb"] = int(color)


def from_config():
    if os.environ.get("LAMP_DRY"):
        return FakeLamp()
    cfg = json.loads(CONFIG.read_text(encoding="utf-8")).get("lamp") or {}
    if not cfg.get("ip") or len(cfg.get("token", "")) != 32:
        raise RuntimeError("лампа не налаштована (немає ip/token у config.json)")
    return Lamp(cfg["ip"], cfg["token"])


def discover(timeout=4):
    # ip of every miio device in the network: broadcast hello to port 54321
    hello = bytes.fromhex("21310020" + "ff" * 28)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(0.5)
    found = set()
    try:
        for dst in ("255.255.255.255", "192.168.0.255", "192.168.1.255"):
            try:
                s.sendto(hello, (dst, PORT))
            except OSError:
                pass
        end = time.time() + timeout
        while time.time() < end:
            try:
                _, addr = s.recvfrom(1024)
                found.add(addr[0])
            except socket.timeout:
                pass
    finally:
        s.close()
    return sorted(found)


def relocate():
    # after an outage the router may give the lamp a new address: find the device that accepts
    # our token (only reads the state, no blinking) and save the new ip
    full = json.loads(CONFIG.read_text(encoding="utf-8"))
    cfg = full.get("lamp") or {}
    for ip in discover():
        if ip == cfg.get("ip"):
            continue
        cand = Lamp(ip, cfg["token"])
        try:
            cand.state()
        except Exception:
            continue
        cfg["ip"] = ip
        full["lamp"] = cfg
        CONFIG.write_text(json.dumps(full, ensure_ascii=False, indent=2), encoding="utf-8")
        return cand
    return None


def robust():
    lamp = from_config()
    if isinstance(lamp, FakeLamp):
        return lamp
    try:
        lamp.state()
        return lamp
    except Exception:
        found = relocate()
        if found:
            return found
        raise


if __name__ == "__main__":
    lamp = robust()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "toggle"
    if cmd == "on":
        lamp.power(True)
    elif cmd == "off":
        lamp.power(False)
    elif cmd == "toggle":
        lamp.toggle()
    elif cmd == "bright":
        lamp.bright(sys.argv[2])
    elif cmd == "ct":
        lamp.ct(sys.argv[2])
    elif cmd == "rgb":
        lamp.rgb(int(sys.argv[2], 16))
    elif cmd == "info":
        print(lamp.dev.call("miIO.info"))
    print(lamp.model, lamp.state())
