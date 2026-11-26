import datetime as dt
import time

from app import keyboards as kb
from app.log import log
from app.services import mono, shifts
from app.telegram import Ctx

HELP = (
    "💰 Щоб бот бачив гроші, потрібен токен monobank (один раз):\n"
    "1) Відкрий api.monobank.ua на компі, увійди через QR у застосунку mono.\n"
    "2) Скопіюй токен.\n"
    "3) Відкрий C:\\Soft\\EcoMode\\bot\\config.json і заміни ВСТАВ_СЮДИ на токен (лапки залиш).\n"
    "Токен дає лише ПЕРЕГЛЯД виписки — переказувати ним гроші не можна. Нікому його не пересилай."
)
SYNC_EVERY = 3600
RETRY_AFTER = 900

# called after every successful sync (the panel keeps its cache fresh this way)
on_synced = []
_last_try = 0.0


def age() -> float:
    return time.time() - mono.INFO_PATH.stat().st_mtime if mono.INFO_PATH.exists() else 1e9


MONTHS = ["січень", "лютий", "березень", "квітень", "травень", "червень", "липень", "серпень", "вересень", "жовтень", "листопад", "грудень"]  # fmt: skip


def uah(n: float) -> str:
    return f"{n:,.0f}".replace(",", " ")


def salary(cfg: dict) -> list:
    # estimates from the shift schedule: the month still to be paid (till payday), this one and the next if known
    pay, today = cfg.get("pay"), dt.date.today()
    if not pay:
        return []
    prev = (today.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    nxt = (today.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    months = ([prev] if today.day <= pay.get("payday", 7) else []) + [today.replace(day=1), nxt]
    out = []
    for m in months:
        s = shifts.salary(m.year, m.month, pay)
        if s:
            paid = (m.replace(day=28) + dt.timedelta(days=4)).replace(day=pay.get("payday", 7))
            h = s["hours"]
            out.append(
                {
                    "month": MONTHS[m.month - 1],
                    "total": s["total"],
                    "base": s["base"],
                    "extra": s["extra"],
                    "kpi": s["kpi"],
                    "hours": round(sum(h.values())),
                    "extra_hours": round(h["extra_day"] + h["extra_night"]),
                    "paid": f"{paid:%d.%m}",
                }
            )
    return out


def salary_text(cfg: dict) -> str:
    lines = [
        f"• {s['month']}: {s['hours']} год (доп. {s['extra_hours']}) ≈ {uah(s['total'])} грн, прийде ~{s['paid']}"
        for s in salary(cfg)
    ]
    return "💼 Зарплата за графіком (орієнтовно, з KPI):\n" + "\n".join(lines) if lines else ""


def summary(cfg: dict) -> str:
    if not mono.token():
        return HELP
    s = mono.summary(cfg.get("daily_budget", 280))
    if s and (pay := salary_text(cfg)):
        s += "\n\n" + pay
    return s or "⏳ Ще не тягнув виписку — зараз підтягну (≈ 2 хв, у mono ліміт 1 запит на хвилину)."


def sync(ctx: Ctx | None = None) -> bool:
    # ctx means someone asked in the chat and wants to hear back
    def done(err):
        if err:
            log(f"mono: {err!r}")
        else:
            for fn in on_synced:
                try:
                    fn()
                except Exception as e:
                    log(f"after sync: {e!r}")
        if ctx and err:
            ctx.send(f"💰 Не вийшло оновити виписку: {err}", menu=kb.MAIN)
        elif ctx:
            ctx.send("🔄 Виписку оновлено.\n\n" + summary(ctx.cfg), menu=kb.MAIN)

    started = mono.sync_in_background(31, done)
    if started and ctx:
        ctx.send("🔄 Тягну виписку з mono, це ≈ 2 хв…", menu=kb.MAIN)
    return started


def handle(ctx: Ctx, text: str) -> bool:
    if text == kb.MONEY:
        ctx.send(summary(ctx.cfg), menu=kb.MAIN)
        if mono.token() and age() > 3 * 3600:
            sync(ctx)
    elif text == kb.SYNC:
        if not mono.token():
            ctx.send(summary(ctx.cfg), menu=kb.MAIN)
        elif not sync(ctx):
            ctx.send("⏳ Виписка вже оновлюється, зачекай хвилинку.", menu=kb.MAIN)
    else:
        return False
    return True


def tick(ctx: Ctx) -> None:
    global _last_try
    if mono.token() and not mono.is_syncing() and age() > SYNC_EVERY and time.time() - _last_try > RETRY_AFTER:
        _last_try = time.time()
        sync()
