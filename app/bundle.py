# moves the bot's settings and state to the hub without putting secrets in a chat:
#   python -m app.bundle pack            on the laptop: hub.bundle (encrypted) + a passphrase on the screen
#   python -m app.bundle pack --only .env xiaomi_session.json   just some files for a hub that already runs
#   python -m app.bundle unpack FILE     on the hub: asks for the passphrase, writes config.json, .env and state
# send the file any way you like, the passphrase another way (a call)
import getpass
import hashlib
import io
import json
import os
import secrets
import sys
import time
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app import sync
from app.paths import CONFIG, ROOT
from app.services import powerlog

EXTRA = [".env", "power.db", "station_history.csv", "xiaomi_session.json"]
LAPTOP_ONLY = ["lamp", "mono_dir", "role"]  # the lamp is reached through the agent, mono_dir is a laptop path


def _key(passphrase: str, salt: bytes) -> bytes:
    return hashlib.scrypt(passphrase.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 2**20, dklen=32)


def pack(out: Path, only: list | None = None) -> None:
    # only: just these files for a hub that already runs (its state and power log stay)
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    for k in LAPTOP_ONLY:
        cfg.pop(k, None)
    cfg["role"] = "hub"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if not only:
            z.writestr("config.json", json.dumps(cfg, ensure_ascii=False, indent=2))
        for name in only or sync.FILES + EXTRA:
            if (ROOT / name).exists():
                z.write(ROOT / name, name)
    passphrase = "-".join(secrets.token_hex(3) for _ in range(4))
    salt, nonce = os.urandom(16), os.urandom(12)
    out.write_bytes(b"HB1" + salt + nonce + AESGCM(_key(passphrase, salt)).encrypt(nonce, buf.getvalue(), None))
    print(f"{out}\nпароль: {passphrase}")


def unpack(src: Path) -> None:
    raw = src.read_bytes()
    if raw[:3] != b"HB1":
        sys.exit("це не home-bot bundle")
    salt, nonce, body = raw[3:19], raw[19:31], raw[31:]
    try:
        data = AESGCM(_key(getpass.getpass("пароль: ").strip(), salt)).decrypt(nonce, body, None)
    except Exception:
        sys.exit("пароль не підходить")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            path = ROOT / info.filename
            if info.filename == "power.db" and path.exists():
                # the hub's own log stays, only the missing rows come in
                tmp = ROOT / "power_import.db"
                tmp.write_bytes(z.read(info))
                samples, events = powerlog.merge(tmp)
                tmp.unlink()
                print(f"ok power.db: +{samples} samples, +{events} events")
                continue
            path.write_bytes(z.read(info))
            # keep the time: the newer state wins when the bot starts (app.sync)
            ts = time.mktime(info.date_time + (0, 0, -1))
            os.utime(path, (ts, ts))
            print("ok", info.filename)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["pack"]:
        only = args[args.index("--only") + 1 :] if "--only" in args else None
        rest = args[1 : args.index("--only")] if only else args[1:]
        pack(Path(rest[0]) if rest else ROOT / "hub.bundle", only)
    elif len(args) == 2 and args[0] == "unpack":
        unpack(Path(args[1]))
    else:
        sys.exit("python -m app.bundle pack [FILE] [--only NAME ...] | unpack FILE")
