# home-bot

Telegram bot that runs on my laptop: power modes, Xiaomi lamp, Oukitel power station, power outage schedule (DTEK),
reminders, pills and monobank statement. Has a Telegram Mini App panel: [home-bot-panel](https://github.com/dxrrkwm/home-bot-panel).

Standard library only, except `cryptography` (lamp) and `pycryptodome` (station).

## Run

```
python bot.py
```

In production it is started by `watchdog.ps1` (Windows Task Scheduler, every 3 min): it restarts the bot
if the process is gone, the heartbeat is older than 6 minutes or memory goes over 500 MB.

## Structure

```
bot.py                 entry point
app/
  main.py              polling loop, single instance, crash logging
  router.py            update -> feature, aliases, background ticks
  telegram.py          bot api, Ctx.send (can be redirected to the panel), outbox for messages sent without internet
  keyboards.py         buttons and menus
  panel.py             mini app bridge over ntfy, encrypted answers
  config.py, paths.py, log.py
  features/            one module per feature: handle(ctx, text) -> bool, optional start() and tick(ctx)
  services/            integrations without telegram: lamp, mono, station, outage, pills, planner, powerlog
```

## Power station modes

`station_ups` in `config.json` (or `ups on` / `ups off` in the chat):

- `true`: the station is always plugged in, router and laptop run through it (UPS, switchover < 10 ms).
  Outages are detected by the station (no input and it supplies the load, 2 polls in a row): eco on, normal when power is back.
  AC is never switched off on idle. Alerts: 30/15%, AC off, station offline 10 min, overheating, fault code.
- `false`: the station stands aside, outages are detected by the laptop going to battery.

## Power log

`power.db` (sqlite, not in git): a sample every minute from `features/power.py` (station %, mode, AC, in/out W,
laptop on mains or battery, eco, internet) and events (laptop_battery/mains, station_supply/charging/idle,
station_online/offline, ac_on/off, mode_eco/normal, net_up/down). Summary of outages and drain/charge rates:

```
python -m app.services.powerlog 7
```

New feature: add `app/features/<name>.py` with `handle`, then put it into `FEATURES`
(and `BACKGROUND` if it has `start` or `tick`) in `router.py`. Order in `FEATURES` matters: first match answers.

## Config

`config.json`, `.env` and `planner.json` (monthly reminders and birthdays) are not in git,
see `config.example.json`, `.env.example` and `planner.example.json`.
`watchdog.ps1` uses Python 3.13 from `%LOCALAPPDATA%\Programs\Python`, or `HOME_BOT_PYTHON` if it is set.

## Lint

```
ruff check . && ruff format --check .
```
