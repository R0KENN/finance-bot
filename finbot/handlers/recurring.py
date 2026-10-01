"""Регулярные операции: зарплата, подписки, аренда — создаются сами по расписанию."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..repos import Repos
from ..repos.users import Profile
from ..services import dates
from ..services.money import parse_amount
from ..states import RecurS
from ..ui.common import CB, TEXT, Screen, btn, grid, kb, show
from ..ui.format import cmoney, esc, kind_icon, label
from ..ui.nav import section
from ..ui.prompts import answer_in_place, ask, retry

router = Router(name="recurring")

FREQ = {"daily": "каждый день", "weekly": "каждую неделю", "monthly": "каждый месяц"}


def schedule_text(freq: str, param: int) -> str:
    if freq == "daily":
        return "каждый день"
    if freq == "weekly":
        return f"каждый {dates.WEEKDAYS[param]}"
    return "в последний день месяца" if param >= 31 else f"{param}-го числа каждого месяца"


async def recurring_screen(repos: Repos, profile: Profile) -> Screen:
    items = await repos.recurring.list(profile.id)
    lines = ["🔁 <b>Регулярные операции</b>", ""]
    if not items:
        lines.append("Зарплата, аренда, подписки — запиши один раз, и операции будут "
                     "создаваться сами в нужный день.")
    buttons = []
    for item in items:
        state = "" if item["active"] else " ⏸"
        category = label(item["cat_emoji"], item["cat_name"]) or "—"
        what = f"{item['note']} · {category}" if item["note"] else category
        lines.append(f"{kind_icon(item['kind'])} <b>{esc(what)}</b> {cmoney(item['acc_currency'], item['amount'])}{state}\n"
                     f"    {schedule_text(item['freq'], item['param'])}")
        buttons.append(btn(f"{kind_icon(item['kind'])} {item['note'] or item['cat_name'] or 'Операция'}"
                           f" {cmoney(item['acc_currency'], item['amount'])}", "rcd", item["id"]))
    markup = kb(*grid(buttons, 1), [btn("➕ Добавить", "rcn")], [btn("🏠 Меню", "go", "menu")])
    return Screen("\n".join(lines), markup)


@section("recurring")
async def recurring_section(repos: Repos, profile: Profile) -> Screen:
    return await recurring_screen(repos, profile)


@router.callback_query(CB.filter(F.a == "rcd"))
async def on_open(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    item = await repos.recurring.get(profile.id, int(callback_data.x or 0))
    if not item:
        await show(callback, await recurring_screen(repos, profile))
        return
    lines = [f"{kind_icon(item['kind'])} <b>{cmoney(item['acc_currency'], item['amount'])}</b>",
             f"🏷 {esc(label(item['cat_emoji'], item['cat_name']))}"]
    if item["src_name"]:
        lines.append(f"🧾 {esc(item['src_name'])}")
    lines += [f"👛 {esc(item['acc_name'])}", f"🗓 {schedule_text(item['freq'], item['param'])}"]
    if item["note"]:
        lines.append(f"📝 {esc(item['note'])}")
    lines.append(f"▶️ Следующая: {item['next_day']}" if item["active"] else "⏸ На паузе")
    markup = kb([btn("▶️ Включить" if not item["active"] else "⏸ Пауза", "rct", item["id"]),
                 btn("🗑 Удалить", "rcx", item["id"])],
                [btn("◀️ Назад", "go", "recurring")])
    await show(callback, Screen("\n".join(lines), markup))


@router.callback_query(CB.filter(F.a == "rct"))
async def on_toggle(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    await repos.recurring.toggle(profile.id, int(callback_data.x or 0))
    await show(callback, await recurring_screen(repos, profile))


@router.callback_query(CB.filter(F.a == "rcx"))
async def on_delete(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    await repos.recurring.delete(profile.id, int(callback_data.x or 0))
    await show(callback, await recurring_screen(repos, profile))


# ---------------------------------------------------------------- создание

@router.callback_query(CB.filter(F.a == "rcn"))
async def on_new(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.update_data(rc={})
    await show(callback, Screen("🔁 <b>Что будет повторяться?</b>", kb(
        [btn("➖ Расход (подписка, аренда)", "rck", "expense")],
        [btn("➕ Доход (зарплата, аренда)", "rck", "income")],
        [btn("✖️ Отмена", "go", "recurring")])))


@router.callback_query(CB.filter(F.a == "rck"))
async def on_kind(callback: CallbackQuery, callback_data: CB, state: FSMContext):
    if callback_data.x not in ("income", "expense"):
        return
    await ask(callback, state, RecurS.amount,
              "🔁 <b>Сумма одной операции</b>\n\nНапример <code>1500</code> или <code>50к</code>.",
              back_to="recurring", rc={"kind": callback_data.x})


async def _rc(state: FSMContext) -> dict | None:
    return (await state.get_data()).get("rc")


@router.message(RecurS.amount, TEXT)
async def on_amount(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    account = await repos.default_account(profile)       # операция создаётся на счёте по умолчанию
    amount = parse_amount(message.text, account["currency"])
    if amount is None:
        await retry(message, state, "Не понял сумму. Пример: 1500 или 50к. "
                                    f"Сумма — в валюте счёта «{account['name']}» ({account['currency']}).")
        return
    rc = await _rc(state)
    rc["amount"] = amount
    await state.update_data(rc=rc)
    cats = await repos.categories.list(profile.id, rc["kind"])
    buttons = [btn(label(c["emoji"], c["name"]), "rcc", c["id"]) for c in cats]
    await answer_in_place(message, state, Screen(
        "🏷 <b>Категория</b>", kb(*grid(buttons, 2), [btn("✖️ Отмена", "go", "recurring")])))


@router.callback_query(CB.filter(F.a == "rcc"))
async def on_category(callback: CallbackQuery, callback_data: CB, state: FSMContext,
                      repos: Repos, profile: Profile):
    rc = await _rc(state)
    category = await repos.categories.get(profile.id, int(callback_data.x or 0))
    if not rc or not category or category["kind"] != rc.get("kind"):
        await callback.answer("Начни заново", show_alert=True)
        return
    rc["category_id"] = category["id"]
    await state.update_data(rc=rc)
    if rc["kind"] == "income":
        sources = await repos.sources.list(profile.id)
        buttons = [btn(label(s["emoji"], s["name"]), "rcs", s["id"]) for s in sources]
        await show(callback, Screen("🧾 <b>Источник дохода</b>", kb(
            *grid(buttons, 2), [btn("⏭ Без источника", "rcs", 0)],
            [btn("✖️ Отмена", "go", "recurring")])))
    else:
        await _ask_freq(callback)


async def _ask_freq(callback: CallbackQuery) -> None:
    await show(callback, Screen("🗓 <b>Как часто?</b>", kb(
        [btn("Каждый день", "rcf", "daily")],
        [btn("Каждую неделю", "rcf", "weekly")],
        [btn("Каждый месяц", "rcf", "monthly")],
        [btn("✖️ Отмена", "go", "recurring")])))


@router.callback_query(CB.filter(F.a == "rcs"))
async def on_source(callback: CallbackQuery, callback_data: CB, state: FSMContext,
                    repos: Repos, profile: Profile):
    rc = await _rc(state)
    source_id = int(callback_data.x or 0)
    if not rc or (source_id and not await repos.sources.get(profile.id, source_id)):
        await callback.answer("Начни заново", show_alert=True)
        return
    rc["source_id"] = source_id or None
    await state.update_data(rc=rc)
    await _ask_freq(callback)


@router.callback_query(CB.filter(F.a == "rcf"))
async def on_freq(callback: CallbackQuery, callback_data: CB, state: FSMContext):
    rc = await _rc(state)
    freq = callback_data.x
    if not rc or freq not in FREQ:
        return
    rc["freq"] = freq
    await state.update_data(rc=rc)
    if freq == "daily":
        rc["param"] = 0
        await state.update_data(rc=rc)
        await _ask_note(callback, state)
    elif freq == "weekly":
        buttons = [btn(name.capitalize(), "rcp", i) for i, name in enumerate(dates.WEEKDAYS)]
        await show(callback, Screen("📆 <b>В какой день недели?</b>", kb(
            *grid(buttons, 2), [btn("✖️ Отмена", "go", "recurring")])))
    else:
        buttons = [btn(str(d), "rcp", d) for d in range(1, 29)]
        await show(callback, Screen("📆 <b>Какого числа?</b>", kb(
            *grid(buttons, 7), [btn("Последний день месяца", "rcp", 31)],
            [btn("✖️ Отмена", "go", "recurring")])))


@router.callback_query(CB.filter(F.a == "rcp"))
async def on_param(callback: CallbackQuery, callback_data: CB, state: FSMContext):
    rc = await _rc(state)
    if not rc or "freq" not in rc:
        await callback.answer("Начни заново", show_alert=True)
        return
    rc["param"] = int(callback_data.x or 0)
    await state.update_data(rc=rc)
    await _ask_note(callback, state)


async def _ask_note(callback: CallbackQuery, state: FSMContext) -> None:
    await ask(callback, state, RecurS.note,
              "📝 <b>Подпись</b>, например «Netflix» или «Аренда». Или «-», если не нужна.",
              back_to="recurring")


@router.message(RecurS.note, TEXT)
async def on_note(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    rc = await _rc(state)
    if not rc or "freq" not in rc:
        await retry(message, state, "Начни заново через меню.")
        return
    note = "" if message.text.strip() in ("-", "—") else message.text.strip()[:100]
    account = await repos.default_account(profile)
    first = dates.first_occurrence(rc["freq"], rc["param"], profile.today)
    await repos.recurring.add(
        profile.id, rc["kind"], rc["amount"], account["id"], rc["freq"], rc["param"], first,
        category_id=rc.get("category_id"), source_id=rc.get("source_id"), note=note)
    screen = await recurring_screen(repos, profile)
    screen.text = f"✅ Готово. Первая операция: {first.strftime('%d.%m.%Y')}\n\n" + screen.text
    await answer_in_place(message, state, screen)
    await state.clear()
