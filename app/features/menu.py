# last in the list: navigation and anything nobody else understood
from app import keyboards as kb
from app.telegram import Ctx

HINT = "Лампа — кнопками внизу, все інше — у «Панель» ліворуч від поля вводу."


def handle(ctx: Ctx, text: str) -> bool:
    if text in (kb.BACK, "/start", "/menu"):
        ctx.send(HINT, menu=kb.MAIN)
    else:
        ctx.send("Не зрозумів 🤔\n\n" + HINT, menu=kb.MAIN)
    return True
