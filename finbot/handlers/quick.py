"""Быстрый ввод: пользователь просто пишет «кофе 250» или «+50000 зарплата»."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from ..repos import Repos
from ..repos.users import Profile
from ..services.money import format_major
from ..services.parser import match_category, match_source, parse_quick
from ..states import Add
from ..ui.common import Screen, btn, drop, kb, send
from ..ui.format import esc
from .add import category_picker, new_draft, pick_account, precision_text, save_draft

router = Router(name="quick")

HINT = (
    "🤔 Не понял. Напиши так:\n"
    "<code>кофе 250</code> — расход\n"
    "<code>+50000 зарплата</code> — доход\n"
    "<code>такси 400 вчера</code> — за другой день\n\n"
    "Или открой меню."
)


@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def on_quick(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    entry = parse_quick(message.text, profile.today)
    if entry is None:
        await message.answer(HINT, reply_markup=kb([btn("🏠 Меню", "go", "menu")]))
        return

    # Одна сумма без слов: спрашиваем, что это
    if not entry.text:
        await drop(message)
        await send(message.bot, message.chat.id, Screen(
            f"💬 <b>{format_major(entry.value)}</b> — что это?",
            kb([btn("➖ Расход", "add", "expense", entry.value),
                btn("➕ Доход", "add", "income", entry.value)],
               [btn("✖️ Отмена", "go", "menu")]),
        ))
        return

    account, created = None, False
    if entry.currency:
        account, created = await pick_account(repos, profile, entry.currency)
        if account is None:
            await drop(message)
            await send(message.bot, message.chat.id, Screen(
                f"💱 Не знаю курс для {entry.currency}: он не загрузился. "
                "Задай свой курс в «Настройки → Валюты и курсы» и повтори запись.",
                kb([btn("💱 Валюты и курсы", "go", "currencies"), btn("🏠 Меню", "go", "menu")])))
            return

    categories = await repos.categories.list(profile.id, entry.kind)
    category = match_category(entry.text, categories, entry.kind)
    draft = await new_draft(repos, profile, entry.kind, value=entry.value,
                            day=entry.day, account=account, note=entry.text)
    if draft["amount"] is None:        # знаков после запятой больше, чем бывает у этой валюты
        await drop(message)
        await send(message.bot, message.chat.id, Screen(
            "⚠️ " + precision_text(draft["currency"]),
            kb([btn("🏠 Меню", "go", "menu")])))
        return
    if entry.kind == "income":
        source = match_source(entry.text, await repos.sources.list(profile.id))
        if source:
            draft["source_id"] = source["id"]

    await drop(message)
    if category:
        draft["category_id"] = category["id"]
        if entry.kind == "income" and draft["source_id"] is None:
            draft["source_id"] = 0           # быстрый ввод: источник не уточняем
        screen = await save_draft(repos, profile, draft)
        if created:
            screen.text += (f"\n\n🆕 Создал счёт «{esc(account['name'])}» в валюте "
                            f"{account['currency']}.")
        await send(message.bot, message.chat.id, screen)
        return

    # Категорию угадать не удалось — просим выбрать, остальное уже собрано
    await state.set_state(Add.card)
    sent = await send(message.bot, message.chat.id, await category_picker(repos, profile, draft))
    await state.update_data(draft=draft, msg=sent.message_id)
