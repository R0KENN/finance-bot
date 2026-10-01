"""Долги: кто должен мне, кому должен я, сроки и частичные погашения."""
from __future__ import annotations

from datetime import date

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..repos import Repos
from ..repos.users import Profile
from ..services import dates
from ..services.mascot import with_mascot
from ..services.money import bar, parse_amount
from ..states import DebtS
from ..ui.common import CB, TEXT, Screen, btn, grid, kb, show
from ..ui.format import esc, money
from ..ui.nav import section
from ..ui.prompts import answer_in_place, ask, retry

router = Router(name="debts")

TITLE = {"owe": "📤 Я должен", "owed": "📥 Мне должны"}


def _due_text(debt, today: date) -> str:
    if not debt["due_day"]:
        return ""
    due = date.fromisoformat(debt["due_day"])
    days = (due - today).days
    if days < 0:
        return f" · ⏰ просрочен на {-days} дн."
    if days == 0:
        return " · ⏰ срок сегодня"
    return f" · до {dates.fmt_day(due, today)}"


async def debts_screen(repos: Repos, profile: Profile) -> Screen:
    today = profile.today
    debts = await repos.debts.list(profile.id)
    owe, owed = await repos.debts.remaining(profile.id)
    lines = ["🤝 <b>Долги</b>", ""]
    if not debts:
        lines += ["Записывай, кто тебе должен и кому должен ты: срок, частичные погашения, "
                  "остаток. В срок напомню.", ""]
    lines += [f"📤 Я должен: <b>{money(profile, owe)}</b>",
              f"📥 Мне должны: <b>{money(profile, owed)}</b>"]
    buttons = []
    for direction in ("owed", "owe"):
        group = [d for d in debts if d["direction"] == direction]
        if not group:
            continue
        lines += ["", f"<b>{TITLE[direction]}</b>"]
        for debt in group:
            left = debt["amount"] - debt["paid"]
            lines.append(f"{bar(debt['paid'] / debt['amount'], 8)} <b>{esc(debt['person'])}</b> · "
                         f"{money(profile, left)} из {money(profile, debt['amount'])}"
                         f"{_due_text(debt, today)}")
            buttons.append(btn(f"{'📥' if direction == 'owed' else '📤'} {debt['person'][:24]} "
                               f"· {money(profile, left)}", "dbo", debt["id"]))
    lines += ["", "<i>Учёт долгов отдельный: деньги со счетов не двигаются. "
                  "Саму выдачу или возврат запиши обычной операцией.</i>"]
    markup = kb(*grid(buttons, 1),
                [btn("➕ Мне должны", "dbn", "owed"), btn("➕ Я должен", "dbn", "owe")],
                [btn("🏠 Меню", "go", "menu")])
    return Screen("\n".join(lines), markup)


@section("debts")
async def debts_section(repos: Repos, profile: Profile) -> Screen:
    return await debts_screen(repos, profile)


async def debt_screen(repos: Repos, profile: Profile, debt_id: int) -> Screen | None:
    debt = await repos.debts.get(profile.id, debt_id)
    if not debt:
        return None
    left = debt["amount"] - debt["paid"]
    lines = [f"{TITLE[debt['direction']]} · <b>{esc(debt['person'])}</b>", "",
             f"Сумма: {money(profile, debt['amount'])}",
             f"Погашено: {money(profile, debt['paid'])}",
             f"{bar(debt['paid'] / debt['amount'], 12)} {debt['paid'] / debt['amount'] * 100:.0f}%",
             f"Осталось: <b>{money(profile, left)}</b>"]
    due = _due_text(debt, profile.today)
    if due:
        lines.append(due.lstrip(" ·"))
    if debt["closed"]:
        lines.append("✅ Закрыт")
    rows = []
    if not debt["closed"]:
        rows.append([btn("💸 Частичное погашение", "dbp", debt_id)])
        rows.append([btn("✅ Погашен полностью", "dbc", debt_id)])
    rows.append([btn("🗑 Удалить", "dbx", debt_id), btn("◀️ Назад", "go", "debts")])
    return Screen("\n".join(lines), kb(*rows))


@router.callback_query(CB.filter(F.a == "dbo"))
async def on_open(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    screen = await debt_screen(repos, profile, int(callback_data.x or 0))
    await show(callback, screen or await debts_screen(repos, profile))


# ---------------------------------------------------------------- создание

@router.callback_query(CB.filter(F.a == "dbn"))
async def on_new(callback: CallbackQuery, callback_data: CB, state: FSMContext):
    if callback_data.x not in TITLE:
        return
    who = "Кто тебе должен?" if callback_data.x == "owed" else "Кому ты должен?"
    await ask(callback, state, DebtS.person, f"{TITLE[callback_data.x]}\n\n{who} Имя или название.",
              back_to="debts", direction=callback_data.x)


@router.message(DebtS.person, TEXT)
async def on_person(message: Message, state: FSMContext):
    person = message.text.strip()
    if not person or len(person) > 60:
        await retry(message, state, "Имя — от 1 до 60 символов.")
        return
    prompt = f"🤝 <b>{esc(person)}</b>\n\nНа какую сумму? Например <code>15000</code> или <code>15к</code>."
    await state.update_data(person=person, prompt=prompt)
    await state.set_state(DebtS.amount)
    await answer_in_place(message, state, Screen(prompt, kb([btn("✖️ Отмена", "go", "debts")])))


@router.message(DebtS.amount, TEXT)
async def on_amount(message: Message, state: FSMContext):
    amount = parse_amount(message.text)
    if amount is None:
        await retry(message, state, "Не понял сумму. Пример: 15000 или 15к.")
        return
    prompt = ("📅 <b>Срок возврата</b>\n\nДата, например <code>15.11</code> или <code>15.11.2026</code>. "
              "Или «-», если срока нет.")
    await state.update_data(amount=amount, prompt=prompt)
    await state.set_state(DebtS.due)
    await answer_in_place(message, state, Screen(prompt, kb([btn("✖️ Отмена", "go", "debts")])))


@router.message(DebtS.due, TEXT)
async def on_due(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    raw = message.text.strip()
    due = None
    if raw not in ("-", "—"):
        due = dates.parse_user_date(raw, profile.today, allow_future=True)
        if due is None or due < profile.today:
            await retry(message, state, "Не разобрал дату. Пример: 15.11 или «-». Прошлое не подойдёт.")
            return
    data = await state.get_data()
    debt_id = await repos.debts.add(profile.id, data["direction"], data["person"], data["amount"], due)
    await answer_in_place(message, state, await debt_screen(repos, profile, debt_id))
    await state.clear()


# ---------------------------------------------------------------- погашение

@router.callback_query(CB.filter(F.a == "dbp"))
async def on_pay_ask(callback: CallbackQuery, callback_data: CB, state: FSMContext,
                     repos: Repos, profile: Profile):
    debt = await repos.debts.get(profile.id, int(callback_data.x or 0))
    if not debt or debt["closed"]:
        await callback.answer("Долг не найден", show_alert=True)
        return
    left = debt["amount"] - debt["paid"]
    await ask(callback, state, DebtS.pay,
              f"💸 <b>Погашение · {esc(debt['person'])}</b>\n\nОсталось {money(profile, left)}. "
              "Сколько погашено?", back_to="debts", debt_id=debt["id"])


@router.message(DebtS.pay, TEXT)
async def on_pay(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    amount = parse_amount(message.text)
    if amount is None:
        await retry(message, state, "Не понял сумму. Пример: 5000 или 5к.")
        return
    debt_id = (await state.get_data())["debt_id"]
    if not await repos.debts.pay(profile.id, debt_id, amount):
        await retry(message, state, "Больше остатка погасить нельзя.")
        return
    screen = await debt_screen(repos, profile, debt_id)
    if (await repos.debts.get(profile.id, debt_id))["closed"]:
        screen = with_mascot(screen, profile, "debt_closed")
    await answer_in_place(message, state, screen)
    await state.clear()


@router.callback_query(CB.filter(F.a == "dbc"))
async def on_close(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    debt_id = int(callback_data.x or 0)
    if await repos.debts.get(profile.id, debt_id):
        await repos.debts.close(profile.id, debt_id)
        await show(callback, with_mascot(await debt_screen(repos, profile, debt_id), profile, "debt_closed"))
        return
    await show(callback, await debts_screen(repos, profile))


@router.callback_query(CB.filter(F.a == "dbx"))
async def on_delete(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    await repos.debts.delete(profile.id, int(callback_data.x or 0))
    await show(callback, await debts_screen(repos, profile))
