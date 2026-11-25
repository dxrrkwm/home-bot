import re

from app import keyboards as kb
from app.services import pills as meds
from app.telegram import Ctx

PILL_RE = re.compile(r"^\s*(таблетк|ліки|лекарств|пігулк)", re.I)


def buttons() -> list | None:
    rows = [[{"text": f"❌ {m['name']}"[:48], "callback_data": f"delmed:{m['id']}"}] for m in meds.meds()]
    return rows or None


def handle(ctx: Ctx, text: str) -> bool:
    if text.startswith(("took:", "skip:")):
        how, key = text.split(":", 1)
        meds.mark(key, "taken" if how == "took" else "skipped")
        t = key.split("|")[2]
        ctx.send(
            f"✅ Записав: {meds.name_of(key)} ({t}) випито."
            if how == "took"
            else f"⏭ Пропущено: {meds.name_of(key)} ({t}). Більше не нагадую про цей прийом.",
            menu=kb.MAIN,
        )
    elif text.startswith("delmed:"):
        meds.delete(text.split(":", 1)[1])
        ctx.send("🗑 Видалив.\n\n" + meds.list_text(), inline=buttons())
    elif text == kb.PILLS:
        ctx.send(meds.list_text(), inline=buttons())
    elif med := (meds.parse_add(text) if text not in kb.BUTTONS else None):
        m = meds.add(med)
        ctx.send(
            f"💊 Додав: {m['name']} — {', '.join(m['times'])}, {meds.freq_text(m)}.\n"
            f"Нагадуватиму, а якщо не відмітиш «✅ Випив» — повторюю кожні {meds.REPEAT_MIN} хв.",
            menu=kb.MAIN,
        )
    elif PILL_RE.match(text):
        ctx.send(
            "Не зрозумів таблетку 🤔 Приклади:\nтаблетка Вітамін D 09:00\n"
            "таблетка Назва 09:00, 21:00\nтаблетка Назва 10:00 кожні 2 дні",
            menu=kb.MAIN,
        )
    else:
        return False
    return True


def tick(ctx: Ctx) -> None:
    for key, name, t, again in meds.due():
        ctx.send(
            f"💊 Час випити: {name} ({t})" + (" — нагадую ще раз" if again else ""),
            inline=[
                [
                    {"text": "✅ Випив", "callback_data": f"took:{key}"},
                    {"text": "⏭ Пропустити", "callback_data": f"skip:{key}"},
                ]
            ],
        )
        meds.pinged(key)
