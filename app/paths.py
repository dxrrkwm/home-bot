from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config.json"
LOG = ROOT / "bot.log"
HEARTBEAT = ROOT / "heartbeat.txt"
REMINDERS = ROOT / "reminders.json"
ECO_PS1 = ROOT.parent / "eco.ps1"
