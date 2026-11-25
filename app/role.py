# which part of the bot runs on this machine, "role" in config.json:
#   standalone  everything on one machine (the laptop), as before
#   hub         telegram, reminders, station, prices, panel; laptop and lamp through the agent
#   agent       laptop and lamp for the hub; covers for the hub when it is down
# python -m app.role [standalone|hub|agent] shows or sets it
import os
import sys

from app import config

ROLES = ("standalone", "hub", "agent")
ROLE = os.environ.get("HOME_BOT_ROLE") or config.load().get("role", "standalone")
HAS_DEVICES = ROLE != "hub"  # laptop and lamp are on this machine


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] not in ROLES:
            sys.exit(f"role: one of {', '.join(ROLES)}")
        cfg = config.load()
        cfg["role"] = sys.argv[1]
        config.save(cfg)
    print(config.load().get("role", "standalone"))
