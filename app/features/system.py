# where the bot runs, its log and updates from github: "/where", "/log", "/update" (the hub also at night)
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
UPDATE_AT = 5  # hour of the nightly update on the hub

_checked = {"day": None}


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


def version() -> str:
    return _git("rev-parse", "--short", "HEAD")


def covering() -> bool:
    # the laptop runs the whole bot because the hub is silent
    return bool(os.environ.get("HOME_BOT_TAKEOVER"))


def where() -> str:
    text = f"🖥 Бот працює на {platform.node()} ({role.ROLE}), версія {version()}."
    if covering():
        text += "\nЦе ноут, який підміняє ПК з ботом."
    if not role.HAS_DEVICES:
        s = link.laptop_state()
        text += "\n💻 Ноут: на зв'язку." if s else f"\n💻 {link.OFFLINE[0].upper()}{link.OFFLINE[1:]}."
    if copies := _copies():
        text += "\n\n⚙️ Процеси:\n" + copies
    return text


def _copies() -> str:
    # every running bot on this pc: two of them make the watchdog restart both
    script = (
        "Get-CimInstance Win32_Process -Filter \"name like 'python%'\" | "
        "? { $_.CommandLine -match '(\\s|\\\\)bot\\.py\"?\\s*$|-m app' } | "
        "% { '{0} <- {1} s{2} {3:dd.MM HH:mm} {4:n0} MB {5} | {6}' -f $_.ProcessId, $_.ParentProcessId, $_.SessionId, "
        "$_.CreationDate, ($_.PrivatePageCount / 1MB), $_.ExecutablePath, $_.CommandLine }; "
        "Get-ScheduledTask | ? { \"$($_.Actions.Execute) $($_.Actions.Arguments)\" -match 'watchdog|bot\\.py' } | "
        "% { 'task {0}{1}: {2}' -f $_.TaskPath, $_.TaskName, $_.Actions.Arguments }"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            creationflags=NO_WINDOW,
            timeout=30,
        )
        return out.stdout.strip()
    except Exception as e:
        return repr(e)


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


def tick(ctx: Ctx) -> None:
    # the hub takes new code from github once a night, nobody has to touch that pc
    today = time.strftime("%Y-%m-%d")
    if role.ROLE != "hub" or time.localtime().tm_hour != UPDATE_AT or _checked["day"] == today:
        return
    _checked["day"] = today
    _git("fetch", "-q")
    if _git("rev-list", "--count", "HEAD..@{u}") not in ("", "0"):
        update(ctx)


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
