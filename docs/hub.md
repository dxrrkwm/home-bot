# home-bot on a second PC (hub)

The bot can run on a PC that is always on (hub), while the laptop keeps only what works locally (agent).

| Where | What |
|---|---|
| hub | telegram, reminders, pills, monthly planner, prices, mono statement, power station (cloud), DTEK schedule, panel over mqtt, power log |
| laptop (agent) | eco / normal modes (eco.ps1, OMEN BIOS), battery and mains, Xiaomi lamp in the LAN, hotkeys (`lamp.vbs`, `eco.vbs`), mono statement copy for `mono_dir` |

They talk over the same mqtt broker and key as the panel (`app/link.py`), no open ports:

- the agent publishes the laptop state every 20 s (retained) and runs calls from the hub: `run_mode`, lamp methods;
- the hub publishes a beat every minute; if it is silent for 10 min, the agent starts the whole bot on the laptop
  (`python -m app`) and stops it when the hub is back;
- the state files (reminders, pills, planner, prices, station flags) follow the running bot: it keeps a sealed copy
  on the broker, the next one to start takes it if it is newer (`app/sync.py`).

Role: `"role"` in `config.json` (`standalone` / `hub` / `agent`), `python -m app.role hub` sets it.

## No power or internet at home

- UPS mode: the station keeps the router and the laptop on. The hub sees the outage through the station cloud and
  switches the laptop to eco through the agent.
- No internet at home: the hub keeps working (reminders, chat, panel), the laptop and lamp answer "out of reach".
  If the laptop is on battery and the hub is out of reach for 2 min, the agent switches eco by itself and back when mains return.
- The hub PC is down: in 10 min the laptop runs the bot (with the latest state from the broker) until it is back.

## Install on the hub (Windows 11)

1. On the laptop, in the bot folder (run it yourself, the passphrase is shown once):
   ```
   python -m app.bundle pack
   ```
   `hub.bundle` is encrypted (AES-GCM, scrypt): config without the lamp and laptop paths, `.env`, state, power log.
   Send the file in any way, the passphrase in another way (a call).
2. On the hub, PowerShell as the user who is always logged in (no admin needed for the task):
   ```
   irm https://raw.githubusercontent.com/dxrrkwm/home-bot/main/tools/install-hub.ps1 -OutFile $env:TEMP\install-hub.ps1
   powershell -ExecutionPolicy Bypass -File $env:TEMP\install-hub.ps1 -Bundle C:\path\to\hub.bundle
   ```
   It installs Python 3.13 and Git (winget) if missing, clones the repo to `C:\home-bot`, installs the requirements,
   unpacks the bundle (asks for the passphrase), sets the role and adds the task "home-bot watchdog"
   (at startup and every 3 min, S4U: works without a logged on user). At the end it shows the log tail:
   `старт (hub)` means it runs. It warns if the timezone is not UTC+2 or the PC sleeps on idle.
3. On the laptop: `python -m app.role agent`, then restart the bot (stop python with `bot.py`, run `watchdog.ps1`).
4. In the chat: `/where` shows the machine and whether the laptop is online.

## Update

- In the chat: `/update` (git pull, pip, restart within 3 min).
- On the hub: `powershell -ExecutionPolicy Bypass -File C:\home-bot\tools\update-hub.ps1`.
- The laptop agent is the working copy: `git pull` and restart.

## Roll back

- Laptop: `python -m app.role standalone` and restart: everything runs on the laptop as before.
- Hub: `powershell -ExecutionPolicy Bypass -File C:\home-bot\tools\uninstall-hub.ps1 -Purge`
  (removes the task, stops the bot, deletes the folder with the tokens).
