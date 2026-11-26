# home-bot

Telegram bot that runs on my laptop: power modes, Xiaomi lamp, Oukitel power station, power outage schedule (DTEK),
reminders, pills and monobank statement. Has a Telegram Mini App panel: [home-bot-panel](https://github.com/dxrrkwm/home-bot-panel).

Standard library only, except `cryptography` (lamp), `pycryptodome` (station) and `paho-mqtt` (panel).

## Run

```
python bot.py
```

It can also run on a second, always-on PC while the laptop keeps the lamp and the power modes:
see [docs/hub.md](docs/hub.md).

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
  panel.py             mini app commands and answers
  relay.py             mqtt (public broker) and ntfy, sealed with the panel key
  role.py              standalone / hub / agent
  link.py              hub <-> laptop agent: state, calls, hub beat
  agent.py             laptop side when the bot runs on the hub, covers for the hub when it is down
  devices.py           laptop and lamp: local or through the agent
  sync.py              state files follow the running bot through the broker
  bundle.py            encrypted settings and state for a new hub
  config.py, paths.py, env.py, log.py
  features/            one module per feature: handle(ctx, text) -> bool, optional start() and tick(ctx)
  services/            integrations without telegram: laptop, lamp, mono, station, outage, pills, planner, powerlog, prices, sleep
```

## Power station modes

`station_ups` in `config.json` (or `ups on` / `ups off` in the chat):

- `true`: the station is always plugged in, router and laptop run through it (UPS, switchover < 10 ms).
  Outages are detected by the station (no input and it supplies the load, 2 polls in a row): eco on, normal when power is back.
  AC is never switched off on idle. Alerts: 30/15%, AC off, station offline 10 min, overheating, fault code.
- `false`: the station stands aside, outages are detected by the laptop going to battery.

## Snack prices

`features/prices.py` + `services/prices.py`: energy drinks 0.5 l and bars of the brands in `prices.json`
(copy of `prices.example.json`, not in git) at Silpo (sf-ecom-api) and Novus (zakaz.ua), both open json apis.
Drinks are compared per can, bars per 100 g, "from N pieces" offers count when a pack (12) covers them.
Every 4 hours in the daytime: anything at or below the target that was not announced yet (or got cheaper)
comes as one message with a picture and shop links. Chat: `ціни`, `оновити ціни`, `ціль енергетик 33`,
`ціль батончик 55`. Panel: `prices-panel`, `prices-panel:force`.

## Sleep

`features/sleep.py` + `services/sleep.py`: sleep from the Xiaomi band through the Mi Fitness cloud
(daily reports: phases, score, heart rate, naps). Log in once with `python -m app.services.sleep login`
(`XIAOMI_USER` / `XIAOMI_PASSWORD` from `.env`); if Xiaomi asks to confirm the device, a code comes by email:
`python -m app.services.sleep code 123456`. Only the session is kept, in `xiaomi_session.json` (not in git),
it refreshes itself for months. Every 30 min the bot pulls the last days; a new sleep longer than 1.5 h
comes as a short message (shift work: a "night" can be in the day). Chat: `сон`, `оновити сон`.
Panel: `sleep-panel`, `sleep-panel:force` (last sleep, 14 days of hours, when the sleep was).

## Band: sleep mode and shifts

`features/band.py`, `services/sleepmode.py`, `services/shifts.py`. `сплю` (also a quick reply on the band to any
bot message): lamp off, laptop to eco, every message silent, reminders and pills wait. `встав`: warm light from 1%
up, normal mode back, a short brief (how long you slept, the shift, pills, reminders, outages today). The mode ends
by itself when the band shows the sleep finished or after 14 h. Shifts (12 h, day 09–21, night 21–09) are set in
the panel calendar (`shift:YYYY-MM-DD` cycles day, night, none; `shifts.json`, not in git); the bot tells when to go
to bed before a day shift and when to nap before a night one. Panel: `band-panel`.

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

`config.json`, `.env`, `planner.json` (monthly reminders and birthdays) and `prices.json` are not in git,
see `config.example.json`, `.env.example`, `planner.example.json` and `prices.example.json`.
`watchdog.ps1` uses Python 3.13 from `%LOCALAPPDATA%\Programs\Python`, or `HOME_BOT_PYTHON` if it is set.

## Lint

```
ruff check . && ruff format --check .
```
