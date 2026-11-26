from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config.json"
LOG = ROOT / "bot.log"
HEARTBEAT = ROOT / "heartbeat.txt"
TAKEOVER_HEARTBEAT = ROOT / "heartbeat_takeover.txt"  # the agent watches the bot it started
REMINDERS = ROOT / "reminders.json"
ECO_PS1 = ROOT.parent / "eco.ps1"
