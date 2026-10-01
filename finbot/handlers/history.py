"""История операций: фильтры, листание по месяцам, поиск, правка и удаление."""
from __future__ import annotations

import math
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..repos import Repos
from ..repos.users import Profile
from ..services import dates
from ..services.money import decimals_of, parse_amount
from ..states import Hist, TxEdit
from ..ui.common import CB, TEXT, Screen, btn, grid, kb, show
from ..ui.format import esc, label, money, pager, tx_card, tx_line
from ..ui.nav import section
from ..ui.prompts import answer_in_place, ask, retry

router = Router(name="history")

PAGE = 8
KINDS = {"a": (None, "Все"), "i": ("income", "➕ Доходы"), "e": ("expense", "➖ Расходы"),
         "t": ("transfer", "🔄 Переводы")}


async def history_screen(repos: Repos, profile: Profile, period: str = "m",
                         kind: str = "a", page: int = 0) -> Screen:
    uid, today = profile.id, profile.today
    kind = kind if kind in KINDS else "a"
    start, end, title = dates.resolve(period, today)
    kind_filter = KINDS[kind][0]
    total = await repos.transactions.count(uid, start, end, kind=kind_filter)
    pages = max(1, math.ceil(total / PAGE))
    page = max(0, min(page, pages - 1))
    rows = await repos.transactions.list(uid, start, end, kind=kind_filter,
                                         limit=PAGE, offset=page * PAGE)
    income, expense = await repos.transactions.totals(uid, start, end)

    lines = [f"🧾 <b>История · {title}</b>", ""]
    if not rows:
        lines.append("Здесь пока пусто.")
    for number, row in enumerate(rows, 1):
        lines.append(f"<b>{number}.</b> {tx_line(row, profile, today)}")
    lines += ["", f"▲ {money(profile, income)}   ▼ {money(profile, expense)}   "
                  f"· записей: {total}"]

    open_buttons = [btn(str(n), "tx", row["id"]) for n, row in enumerate(rows, 1)]
    filters = [btn(("• " if key == kind else "") + name, "hist", period, key, 0)
               for key, (_, name) in KINDS.items()]
    markup_rows = grid(open_buttons, 4)
    nav = pager(page, pages, "hist", period, kind)
    if nav:
        markup_rows.append(nav)
    markup_rows.append(filters[:2])
    markup_rows.append(filters[2:])
    if dates.is_month_period(period):
        prev_key = dates.shift_month_key(period, -1, today)
        next_key = dates.shift_month_key(period, 1, today)
        month_row = [btn("◀️", "hist", prev_key, kind, 0), btn("📅 Всё время", "hist", "a", kind, 0)]
        if next_key <= dates.month_key(today):
            month_row.append(btn("▶️", "hist", next_key, kind, 0))
        markup_rows.append(month_row)
    else:
        markup_rows.append([btn("📅 Этот месяц", "hist", "m", kind, 0)])
    markup_rows.append([btn("🔎 Поиск", "hsearch"), btn("🏠 Меню", "go", "menu")])
    return Screen("\n".join(lines), kb(*markup_rows))


@section("hist")
async def hist_section(repos: Repos, profile: Profile) -> Screen:
    return await history_screen(repos, profile)


@router.callback_query(CB.filter(F.a == "hist"))
async def on_hist(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    page = int(callback_data.z) if callback_data.z.isdigit() else 0
    await show(callback, await history_screen(
        repos, profile, callback_data.x or "m", callback_data.y or "a", page))


# ---------------------------------------------------------------- поиск

@router.callback_query(CB.filter(F.a == "hsearch"))
async def on_search(callback: CallbackQuery, state: FSMContext):
    await ask(callback, state, Hist.search,
              "🔎 <b>Поиск по истории</b>\n\nНапиши слово из заметки, категории или источника.",
              back_to="hist")


@router.message(Hist.search, TEXT)
async def on_search_text(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    query = message.text.strip()[:50]
    rows = await repos.transactions.list(profile.id, dates.ALL_TIME_START, profile.today,
                                         search=query, limit=15)
    lines = [f"🔎 <b>«{esc(query)}»</b>", ""]
    if not rows:
        lines.append("Ничего не нашёл.")
    for number, row in enumerate(rows, 1):
        lines.append(f"<b>{number}.</b> {tx_line(row, profile)}")
    if len(rows) == 15:
        lines.append("\n<i>Показаны последние 15.</i>")
    buttons = [btn(str(n), "tx", row["id"]) for n, row in enumerate(rows, 1)]
    screen = Screen("\n".join(lines), kb(
        *grid(buttons, 5), [btn("🔎 Ещё поиск", "hsearch"), btn("◀️ К истории", "go", "hist")]))
    await answer_in_place(message, state, screen)
    await state.clear()


# ---------------------------------------------------------------- одна операция

async def tx_screen(repos: Repos, profile: Profile, tx_id: int) -> Screen | None:
    row = await repos.transactions.get(profile.id, tx_id)
    if not row:
        return None
    month = row["day"][:7]
    rows = []
    if row["kind"] != "transfer":
        rows.append([btn("✏️ Сумма", "txe", "amount", tx_id), btn("🏷 Категория", "txe", "cat", tx_id)])
        rows.append([btn("📝 Заметка", "txe", "note", tx_id), btn("📅 Дата", "txe", "date", tx_id)])
    else:
        rows.append([btn("✏️ Сумма", "txe", "amount", tx_id)])
    rows.append([btn("🗑 Удалить", "txdel", tx_id)])
    rows.append([btn("◀️ К истории", "hist", month, "a", 0)])
    return Screen(tx_card(row, profile), kb(*rows))


@router.callback_query(CB.filter(F.a == "tx"))
async def on_tx(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    screen = await tx_screen(repos, profile, int(callback_data.x or 0))
    if screen is None:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    await show(callback, screen)


@router.callback_query(CB.filter(F.a == "txe"))
async def on_tx_edit(callback: CallbackQuery, callback_data: CB, state: FSMContext,
                     repos: Repos, profile: Profile):
    field, tx_id = callback_data.x, int(callback_data.y or 0)
    row = await repos.transactions.get(profile.id, tx_id)
    if not row:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    if field == "cat":
        cats = await repos.categories.list(profile.id, row["kind"])
        buttons = [btn(label(c["emoji"], c["name"]), "txc", tx_id, c["id"]) for c in cats]
        await show(callback, Screen("🏷 <b>Новая категория записи</b>",
                                    kb(*grid(buttons, 2), [btn("◀️ Назад", "tx", tx_id)])))
        return
    prompts = {
        "amount": (TxEdit.amount, "✏️ Новая сумма:"),
        "note": (TxEdit.note, "📝 Новая заметка (или «-», чтобы убрать):"),
        "date": (TxEdit.date, "📅 Новая дата: <code>15.09</code>, <code>15.09.2026</code> или «вчера»."),
    }
    if field not in prompts:
        return
    st, text = prompts[field]
    await ask(callback, state, st, text, back_to="hist", tx_id=tx_id)


@router.callback_query(CB.filter(F.a == "txc"))
async def on_tx_category(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    tx_id, cat_id = int(callback_data.x or 0), int(callback_data.y or 0)
    row = await repos.transactions.get(profile.id, tx_id)
    category = await repos.categories.get(profile.id, cat_id)
    if not row or not category or category["kind"] != row["kind"]:
        await callback.answer("Не получилось сменить категорию", show_alert=True)
        return
    await repos.transactions.update(profile.id, tx_id, category_id=cat_id)
    await show(callback, await tx_screen(repos, profile, tx_id))


async def _edited(message: Message, state: FSMContext, repos: Repos, profile: Profile, **fields):
    data = await state.get_data()
    tx_id = data["tx_id"]
    await repos.transactions.update(profile.id, tx_id, **fields)
    screen = await tx_screen(repos, profile, tx_id)
    await answer_in_place(message, state, screen)
    await state.clear()


@router.message(TxEdit.amount, TEXT)
async def on_edit_amount(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    row = await repos.transactions.get(profile.id, (await state.get_data())["tx_id"])
    code = row["acc_currency"] if row else profile.currency
    amount = parse_amount(message.text, code)
    if amount is None:
        await retry(message, state, f"Не понял сумму. Пример: 1500 или 2к. "
                                    f"Знаков после запятой — не больше {decimals_of(code)}.")
        return
    await _edited(message, state, repos, profile, amount=amount)


@router.message(TxEdit.note, TEXT)
async def on_edit_note(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    text = "" if message.text.strip() == "-" else message.text.strip()[:300]
    await _edited(message, state, repos, profile, note=text)


@router.message(TxEdit.date, TEXT)
async def on_edit_date(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    parsed = dates.parse_user_date(message.text, profile.today)
    if parsed is None:
        await retry(message, state, "Не разобрал дату. Пример: 15.09 или «вчера». Будущее нельзя.")
        return
    await _edited(message, state, repos, profile, day=parsed)


@router.callback_query(CB.filter(F.a == "txdel"))
async def on_delete_ask(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    row = await repos.transactions.get(profile.id, int(callback_data.x or 0))
    if not row:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    await show(callback, Screen(
        "🗑 <b>Удалить запись?</b>\n\n" + tx_card(row, profile),
        kb([btn("🗑 Да, удалить", "txdely", row["id"]), btn("◀️ Нет", "tx", row["id"])]),
    ))


@router.callback_query(CB.filter(F.a == "txdely"))
async def on_delete(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    row = await repos.transactions.get(profile.id, int(callback_data.x or 0))
    month = row["day"][:7] if row else "m"
    if row:
        await repos.transactions.delete(profile.id, row["id"])
    await show(callback, await history_screen(repos, profile, month))
