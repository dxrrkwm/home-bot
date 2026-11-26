# where the bot runs, its log and updates from github: "/where", "/log", "/update"
import os
import platform
import subprocess
import sys
import threading
import time

from app import link, role
from app.paths import LOG, ROOT
from app.telegram import Ctx

NO_WINDOW = 0x08000000


def _git(*args: str) -> str:
    # stdin: the agent gives the bot an open pipe, a child that inherits it may wait on it forever
    out = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        creationflags=NO_WINDOW,
        timeout=60,
    )
    return (out.stdout + out.stderr).strip()


def where() -> str:
    text = f"🖥 Бот працює на {platform.node()} ({role.ROLE}), версія {_git('rev-parse', '--short', 'HEAD')}."
    if os.environ.get("HOME_BOT_TAKEOVER"):
        text += "\nЦе ноут, який підміняє ПК з ботом."
    if not role.HAS_DEVICES:
        s = link.laptop_state()
        text += "\n💻 Ноут: на зв'язку." if s else f"\n💻 {link.OFFLINE[0].upper()}{link.OFFLINE[1:]}."
    return text


def tail(lines: int = 25) -> str:
    # the end of the log where the bot runs, the hub's one is not reachable otherwise
    if not LOG.exists():
        return "📄 Лог порожній."
    rows = LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    return "📄 Лог:\n" + "\n".join(r[:200] for r in rows)


def update(ctx: Ctx) -> None:
    before = _git("rev-parse", "--short", "HEAD")
    pulled = _git("pull", "--ff-only")
    after = _git("rev-parse", "--short", "HEAD")
    if before == after:
        ctx.send(f"🔄 Оновлень немає ({after}).\n{pulled[-500:]}")
        return
    deps = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        creationflags=NO_WINDOW,
    )
    note = "" if deps.returncode == 0 else f"\n⚠️ pip: {deps.stderr[-300:]}"
    ctx.send(f"🔄 Оновлено {before} → {after}, перезапускаюсь (до 3 хв).{note}")
    # the watchdog starts the new version
    threading.Thread(target=lambda: (time.sleep(3), os._exit(0)), daemon=True).start()


def handle(ctx: Ctx, text: str) -> bool:
    if text in ("/where", "де бот", "где бот"):
        ctx.send(where())
    elif text in ("/log", "лог"):
        ctx.send(tail())
    elif text in ("/update", "оновити бота", "обновить бота"):
        update(ctx)
    else:
        return False
    return True
