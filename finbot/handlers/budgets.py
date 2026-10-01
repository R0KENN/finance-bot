"""Бюджеты: месячные лимиты на категории и общий лимит."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..repos import Repos
from ..repos.users import Profile
from ..services import dates
from ..services.money import bar, parse_amount
from ..states import BudgetS
from ..ui.common import CB, TEXT, Screen, btn, grid, kb, show
from ..ui.format import esc, label, money
from ..ui.nav import section
from ..ui.prompts import answer_in_place, ask, retry

router = Router(name="budgets")


def _mark(ratio: float) -> str:
    return "🚨" if ratio >= 1 else "⚠️" if ratio >= 0.8 else "🟢"


async def budgets_screen(repos: Repos, profile: Profile) -> Screen:
    today = profile.today
    start, end = dates.month_bounds(today.year, today.month)
    statuses = await repos.budgets.statuses(profile.id, start, end)
    lines = [f"🎚 <b>Бюджеты · {dates.MONTHS_NOM[today.month - 1]}</b>", ""]
    rows = []
    if not statuses:
        lines += [
            "Лимиты помогают не тратить лишнего: задай сумму на месяц для категории "
            "или общий лимит — и я предупрежу, когда он будет близко.",
        ]
    for budget, spent in statuses:
        ratio = spent / budget["amount"]
        name = "🧮 Общий лимит" if budget["category_id"] is None else (
            label(budget["cat_emoji"], budget["cat_name"]))
        left = budget["amount"] - spent
        tail = (f"осталось {money(profile, left)}" if left >= 0
                else f"превышен на {money(profile, -left)}")
        lines += [f"{_mark(ratio)} <b>{esc(name)}</b>",
                  f"{bar(ratio, 10)} {ratio * 100:.0f}%",
                  f"{money(profile, spent)} из {money(profile, budget['amount'])} · {tail}", ""]
        rows.append(btn(f"⚙️ {name}", "bgo", budget["id"]))
    markup = kb(*grid(rows, 2),
                [btn("➕ Лимит на категорию", "bgn")],
                [btn("🧮 Общий лимит", "bgc", 0), btn("🏠 Меню", "go", "menu")])
    return Screen("\n".join(lines).rstrip(), markup)


@section("budgets")
async def budgets_section(repos: Repos, profile: Profile) -> Screen:
    return await budgets_screen(repos, profile)


@router.callback_query(CB.filter(F.a == "bgn"))
async def on_new(callback: CallbackQuery, repos: Repos, profile: Profile):
    cats = await repos.categories.list(profile.id, "expense")
    limited = {b["category_id"] for b in await repos.budgets.list(profile.id)}
    buttons = [btn(("✅ " if c["id"] in limited else "") + label(c["emoji"], c["name"]), "bgc", c["id"])
               for c in cats]
    await show(callback, Screen("🎚 <b>Для какой категории лимит?</b>",
                                kb(*grid(buttons, 2), [btn("◀️ Назад", "go", "budgets")])))


@router.callback_query(CB.filter(F.a == "bgc"))
async def on_choose_category(callback: CallbackQuery, callback_data: CB, state: FSMContext,
                             repos: Repos, profile: Profile):
    category_id = int(callback_data.x or 0) or None
    if category_id and not await repos.categories.get(profile.id, category_id):
        await callback.answer("Такой категории нет", show_alert=True)
        return
    name = "общий лимит на месяц" if category_id is None else "лимит на категорию"
    await ask(callback, state, BudgetS.amount,
              f"🎚 <b>{name.capitalize()}</b>\n\nСколько можно тратить за месяц? "
              f"Например <code>30000</code> или <code>30к</code>.",
              back_to="budgets", category_id=category_id)


@router.message(BudgetS.amount, TEXT)
async def on_amount(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    amount = parse_amount(message.text)
    if amount is None:
        await retry(message, state, "Не понял сумму. Пример: 30000 или 30к.")
        return
    data = await state.get_data()
    await repos.budgets.set_limit(profile.id, data.get("category_id"), amount)
    await answer_in_place(message, state, await budgets_screen(repos, profile))
    await state.clear()


@router.callback_query(CB.filter(F.a == "bgo"))
async def on_open(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    budget = await repos.budgets.get(profile.id, int(callback_data.x or 0))
    if not budget:
        await show(callback, await budgets_screen(repos, profile))
        return
    name = "Общий лимит" if budget["category_id"] is None else label(budget["cat_emoji"], budget["cat_name"])
    await show(callback, Screen(
        f"🎚 <b>{esc(name)}</b>\nЛимит: {money(profile, budget['amount'])} в месяц",
        kb([btn("✏️ Изменить", "bgc", budget["category_id"] or 0),
            btn("🗑 Удалить", "bgd", budget["id"])],
           [btn("◀️ Назад", "go", "budgets")]),
    ))


@router.callback_query(CB.filter(F.a == "bgd"))
async def on_delete(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    await repos.budgets.delete(profile.id, int(callback_data.x or 0))
    await show(callback, await budgets_screen(repos, profile))
