# is the phone on the home wi-fi: arp from the laptop (the wi-fi chip answers arp even when the phone sleeps).
# the phone is found by its mac, the ip it got from the router does not matter
import ctypes
import socket
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from app import config
from app.log import log

CHECK_SEC = 30
SWEEP_SEC = 120  # look through the whole network not more often than this while the phone is gone
HOSTS = range(2, 255)

_seen = {"ts": 0.0, "ip": None, "swept": 0.0}


def _arp(ip: str) -> str | None:
    dest = struct.unpack("<I", socket.inet_aton(ip))[0]
    mac = (ctypes.c_ubyte * 6)()
    size = ctypes.c_ulong(6)
    if ctypes.windll.iphlpapi.SendARP(dest, 0, mac, ctypes.byref(size)):
        return None
    return "-".join(f"{b:02X}" for b in mac[: size.value])


def _lan() -> str | None:
    # the first three parts of this laptop's address, e.g. "192.168.0"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("1.1.1.1", 53))
            return s.getsockname()[0].rsplit(".", 1)[0]
    except OSError:
        return None


def _find(mac: str) -> str | None:
    lan = _lan()
    if not lan:
        return None
    ips = [f"{lan}.{i}" for i in HOSTS]
    with ThreadPoolExecutor(48) as pool:
        for ip, found in zip(ips, pool.map(_arp, ips)):
            if found == mac:
                return ip
    return None


def _mac() -> str:
    return (config.load().get("phone_mac") or "").upper().replace(":", "-")


def _check() -> None:
    mac = _mac()
    if not mac:
        return
    if _seen["ip"] and _arp(_seen["ip"]) == mac:
        _seen["ts"] = time.time()
        return
    if time.time() - _seen["swept"] < SWEEP_SEC:
        return
    _seen["swept"] = time.time()
    if ip := _find(mac):
        _seen.update(ip=ip, ts=time.time())


def _loop() -> None:
    while True:
        try:
            _check()
            _seen["checked"] = True
        except Exception as e:
            log(f"присутність: {e!r}")
        time.sleep(CHECK_SEC)


def start() -> None:
    if _mac():
        threading.Thread(target=_loop, daemon=True, name="presence").start()


def ago() -> int | None:
    # seconds since the phone was last seen at home, None if nobody watches it or the first look is not done
    if not _mac() or not _seen.get("checked"):
        return None
    return int(time.time() - _seen["ts"]) if _seen["ts"] else 10**6
