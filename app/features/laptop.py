import ctypes
import re
import subprocess

from app import keyboards as kb
from app.paths import ECO_PS1
from app.services import powerlog
from app.telegram import Ctx

SAVER_GUID = "637dba06-ebd6-4e7a-9933-1fe199f85bbb"
NO_WINDOW = 0x08000000


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_byte),
        ("BatteryFlag", ctypes.c_byte),
        ("BatteryLifePercent", ctypes.c_byte),
        ("SystemStatusFlag", ctypes.c_byte),
        ("BatteryLifeTime", ctypes.c_ulong),
        ("BatteryFullLifeTime", ctypes.c_ulong),
    ]


class DEVMODE(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32),
        ("dmSpecVersion", ctypes.c_ushort),
        ("dmDriverVersion", ctypes.c_ushort),
        ("dmSize", ctypes.c_ushort),
        ("dmDriverExtra", ctypes.c_ushort),
        ("dmFields", ctypes.c_ulong),
        ("_pos", ctypes.c_long * 4),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", ctypes.c_wchar * 32),
        ("dmLogPixels", ctypes.c_ushort),
        ("dmBitsPerPel", ctypes.c_ulong),
        ("dmPelsWidth", ctypes.c_ulong),
        ("dmPelsHeight", ctypes.c_ulong),
        ("dmDisplayFlags", ctypes.c_ulong),
        ("dmDisplayFrequency", ctypes.c_ulong),
        ("_rest", ctypes.c_ulong * 8),
    ]


def power() -> tuple[bool, int | None]:
    s = SYSTEM_POWER_STATUS()
    ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s))
    pct = s.BatteryLifePercent & 0xFF
    return s.ACLineStatus == 1, (pct if pct <= 100 else None)


def refresh_hz() -> int:
    d = DEVMODE()
    d.dmSize = ctypes.sizeof(DEVMODE)
    ctypes.windll.user32.EnumDisplaySettingsW(None, -1, ctypes.byref(d))
    return d.dmDisplayFrequency


def active_plan() -> tuple[str, str]:
    out = subprocess.run(
        ["powercfg", "/getactivescheme"], capture_output=True, text=True, creationflags=NO_WINDOW
    ).stdout
    m = re.search(r"GUID:\s*([0-9a-f-]+)\s*\((.*)\)", out)
    return (m.group(1), m.group(2)) if m else ("", "?")


def is_eco() -> bool:
    return active_plan()[0] == SAVER_GUID


def run_mode(mode: str) -> None:
    powerlog.event(f"mode_{mode}")
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(ECO_PS1),
            "-Mode",
            mode,
        ],
        creationflags=NO_WINDOW,
        timeout=150,
    )


def status_text() -> str:
    guid, name = active_plan()
    ac, pct = power()
    mode = "🔋 Еко" if guid == SAVER_GUID else "⚡ Звичайний"
    src = "🔌 від мережі/станції" if ac else "🪫 від батареї"
    bat = f"{pct}%" if pct is not None else "—"
    return f"Режим: {mode}\nПлан Windows: {name}\nЕкран: {refresh_hz()} Гц\nЖивлення: {src}\nБатарея: {bat}"


def handle(ctx: Ctx, text: str) -> bool:
    if text in (kb.ECO, "eco"):
        run_mode("eco")
        ctx.send("🔋 Еко увімкнено (Windows + OMEN Eco).\n\n" + status_text())
    elif text in (kb.NORMAL, "normal"):
        run_mode("normal")
        ctx.send("⚡ Звичайний режим (OMEN Performance).\n\n" + status_text())
    elif text in (kb.STATUS, "/status"):
        ctx.send(status_text())
    elif text == kb.PC:
        ctx.send("💻 ПК\n\n" + status_text(), menu=kb.MAIN)
    else:
        return False
    return True
