# the laptop itself: mains or battery, windows power plan, normal / eco / min modes (eco.ps1 next to the bot)
import ctypes
import re
import subprocess

from app.paths import ECO_PS1
from app.services import presence

SAVER_GUID = "637dba06-ebd6-4e7a-9933-1fe199f85bbb"
MIN_GUID = "b0710000-5eed-4e1e-9a1a-000000000001"  # made by eco.ps1 -Mode min
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
        ["powercfg", "/getactivescheme"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        creationflags=NO_WINDOW,
        timeout=20,
    ).stdout
    m = re.search(r"GUID:\s*([0-9a-f-]+)\s*\((.*)\)", out)
    return (m.group(1), m.group(2)) if m else ("", "?")


def mode() -> str:
    guid = active_plan()[0]
    return "min" if guid == MIN_GUID else "eco" if guid == SAVER_GUID else "normal"


def is_eco() -> bool:
    # min saves even more, for the automatic switches it counts as eco
    return mode() != "normal"


def run_mode(mode: str) -> None:
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
        stdin=subprocess.DEVNULL,  # the bot's own stdin can be the agent's pipe
        creationflags=NO_WINDOW,
        timeout=150,
    )


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_sec() -> int:
    # since the last key or mouse input in this session
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) // 1000


def snapshot() -> dict:
    ac, pct = power()
    guid, plan = active_plan()
    m = "min" if guid == MIN_GUID else "eco" if guid == SAVER_GUID else "normal"
    return {
        "ac": ac,
        "pct": pct,
        "eco": m != "normal",
        "mode": m,
        "plan": plan,
        "hz": refresh_hz(),
        "idle": idle_sec(),
        "phone": presence.ago(),
    }
