# laptop and lamp: on this machine when it has them, otherwise through the agent on the laptop
from app import link, role
from app.log import log
from app.services import laptop, powerlog


def laptop_state() -> dict | None:
    return laptop.snapshot() if role.HAS_DEVICES else link.laptop_state()


def power() -> tuple[bool | None, int | None]:
    # (None, None) when the laptop is out of reach
    if role.HAS_DEVICES:
        return laptop.power()
    s = link.laptop_state() or {}
    return s.get("ac"), s.get("pct")


def idle_sec() -> int | None:
    # None when the laptop is out of reach
    if role.HAS_DEVICES:
        return laptop.idle_sec()
    return (link.laptop_state() or {}).get("idle")


def is_eco() -> bool:
    return laptop.is_eco() if role.HAS_DEVICES else bool((link.laptop_state() or {}).get("eco"))


def run_mode(mode: str) -> None:
    powerlog.event(f"mode_{mode}")
    if role.HAS_DEVICES:
        laptop.run_mode(mode)
    else:
        link.call("run_mode", mode, timeout=150)


def switch(mode: str) -> bool:
    # automatic switch on power events: a laptop out of reach must not break the message
    try:
        run_mode(mode)
        return True
    except Exception as e:
        log(f"режим {mode}: {e!r}")
        return False


def lamp():
    if not role.HAS_DEVICES:
        return link.Lamp()
    from app.services import lamp  # needs cryptography, load only when used

    return lamp.robust()
