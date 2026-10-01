"""Счета: остатки, валюты, переводы и обмен между счетами, правка."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..repos import Repos
from ..repos.users import Profile
from ..services.money import CURRENCIES, decimals_of, parse_amount
from ..services.rates import RateUnknown
from ..states import AccountS, CurS
from ..ui.common import CB, TEXT, Screen, btn, grid, kb, show
from ..ui.format import cmoney, esc, label, money, split_emoji, valid_name
from ..ui.nav import section
from ..ui.prompts import answer_in_place, ask, retry

router = Router(name="accounts")


async def _balance_text(repos: Repos, profile: Profile, account) -> str:
    """Остаток в валюте счёта, а для чужой валюты — ещё и в основной."""
    text = cmoney(account["currency"], account["balance"])
    if account["currency"] != profile.currency:
        try:
            base = await repos.rates.to_base(profile.id, account["balance"], account["currency"])
            text += f" (≈{money(profile, base)})"
        except RateUnknown:
            text += " (курс не задан)"
    return text


async def accounts_screen(repos: Repos, profile: Profile) -> Screen:
    accounts = await repos.accounts.list(profile.id)
    lines = ["👛 <b>Счета</b>", ""]
    for account in accounts:
        star = " ⭐" if account["id"] == profile.default_account_id else ""
        lines.append(f"{esc(label(account['emoji'], account['name']))}{star}  "
                     f"<b>{await _balance_text(repos, profile, account)}</b>")
    totals = await repos.accounts.by_currency(profile.id)
    lines.append("")
    if len(totals) > 1:
        lines.append("💱 " + " · ".join(cmoney(code, value) for code, value in totals.items()))
    lines += [f"🏦 Всего: <b>{money(profile, await repos.accounts.total(profile.id))}</b>",
              "<i>⭐ — счёт по умолчанию для новых записей.</i>"]
    buttons = [btn(label(a["emoji"], a["name"]), "acd", a["id"]) for a in accounts]
    markup = kb(*grid(buttons, 2),
                [btn("➕ Новый счёт", "acn"), btn("🔄 Перевод / обмен", "acx")],
                [btn("🏠 Меню", "go", "menu")])
    return Screen("\n".join(lines), markup)


@section("accounts")
async def accounts_section(repos: Repos, profile: Profile) -> Screen:
    return await accounts_screen(repos, profile)


async def account_screen(repos: Repos, profile: Profile, account_id: int) -> Screen | None:
    account = await repos.accounts.get(profile.id, account_id)
    if not account:
        return None
    code = account["currency"]
    text = (f"{esc(label(account['emoji'], account['name']))}\n\n"
            f"Валюта: {CURRENCIES.get(code, ('', code))[0]} {code}\n"
            f"Остаток: <b>{await _balance_text(repos, profile, account)}</b>")
    markup = kb(
        [btn("✏️ Название", "ace", "name", account_id), btn("💰 Исправить остаток", "ace", "balance", account_id)],
        [btn("⭐ По умолчанию", "acstar", account_id), btn("🗑 Скрыть", "acdel", account_id)],
        [btn("◀️ Назад", "go", "accounts")],
    )
    return Screen(text, markup)


@router.callback_query(CB.filter(F.a == "acd"))
async def on_open(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    screen = await account_screen(repos, profile, int(callback_data.x or 0))
    await show(callback, screen or await accounts_screen(repos, profile))


# ---------------------------------------------------------------- новый счёт

@router.callback_query(CB.filter(F.a == "acn"))
async def on_new(callback: CallbackQuery, state: FSMContext):
    await ask(callback, state, AccountS.name,
              "👛 <b>Новый счёт</b>\n\nНазвание, можно с эмодзи: <code>🏦 Сбербанк</code>",
              back_to="accounts")


@router.message(AccountS.name, TEXT)
async def on_new_name(message: Message, state: FSMContext, profile: Profile):
    emoji, name = split_emoji(message.text, "💳")
    if not valid_name(name):
        await retry(message, state, "Название от 1 до 40 символов.")
        return
    await state.update_data(a_emoji=emoji, a_name=name)
    codes = [profile.currency] + [c for c in CURRENCIES if c != profile.currency]
    buttons = [btn(f"{CURRENCIES[c][0]} {CURRENCIES[c][1]}", "accur", c) for c in codes]
    await answer_in_place(message, state, Screen(
        f"👛 <b>{esc(name)}</b>\n\nВ какой валюте этот счёт?",
        kb(*grid(buttons, 2), [btn("✖️ Отмена", "go", "accounts")])))


async def ask_initial(state: FSMContext, currency: str, name: str, *, message=None,
                      callback=None) -> None:
    """Вопрос про стартовый остаток нового счёта (после выбора валюты и, если надо, курса)."""
    prompt = (f"👛 <b>{esc(name)}</b> · {CURRENCIES[currency][0]} {currency}\n\n"
              f"Сколько на счёте сейчас? Напиши сумму в {CURRENCIES[currency][0]} или «0».")
    await state.update_data(a_currency=currency, prompt=prompt, back_to="accounts")
    await state.set_state(AccountS.initial)
    screen = Screen(prompt, kb([btn("✖️ Отмена", "go", "accounts")]))
    if message is not None:
        await answer_in_place(message, state, screen)
    else:
        await state.update_data(msg=callback.message.message_id)
        await show(callback, screen)


@router.callback_query(CB.filter(F.a == "accur"))
async def on_new_currency(callback: CallbackQuery, callback_data: CB, state: FSMContext,
                          repos: Repos, profile: Profile):
    code = callback_data.x
    data = await state.get_data()
    if code not in CURRENCIES or "a_name" not in data:
        await callback.answer("Начни заново", show_alert=True)
        return
    if not await repos.rates.has_rate(profile.id, code):
        # Курса нет ни в сети, ни у пользователя — просим ввести вручную
        await ask(callback, state, CurS.rate,
                  f"💱 Не знаю курс {code} к {profile.currency}.\n"
                  f"Сколько {profile.symbol} стоит 1 {CURRENCIES[code][0]}? Например <code>92,5</code>.",
                  back_to="accounts", rate_for=code, then="account")
        return
    await ask_initial(state, code, data["a_name"], callback=callback)


@router.message(AccountS.initial, TEXT)
async def on_new_initial(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    raw = message.text.strip()
    data = await state.get_data()
    amount = 0 if raw in ("0", "-", "—") else parse_amount(raw, data["a_currency"])
    if amount is None:
        await retry(message, state, "Не понял сумму. Пример: 15000 или «0». "
                                    f"Знаков после запятой — не больше {decimals_of(data['a_currency'])}.")
        return
    await repos.accounts.add(profile.id, data["a_name"], data["a_emoji"], amount, data["a_currency"])
    await answer_in_place(message, state, await accounts_screen(repos, profile))
    await state.clear()


# ---------------------------------------------------------------- правка

@router.callback_query(CB.filter(F.a == "ace"))
async def on_edit(callback: CallbackQuery, callback_data: CB, state: FSMContext,
                  repos: Repos, profile: Profile):
    field, account_id = callback_data.x, int(callback_data.y or 0)
    account = await repos.accounts.get(profile.id, account_id)
    if not account:
        await callback.answer("Счёт не найден", show_alert=True)
        return
    if field == "name":
        await ask(callback, state, AccountS.rename, "✏️ Новое название (можно с эмодзи):",
                  back_to="accounts", account_id=account_id)
    elif field == "balance":
        await ask(callback, state, AccountS.set_balance,
                  f"💰 Сколько на счёте «{esc(account['name'])}» на самом деле?\n"
                  f"Сейчас по учёту: {cmoney(account['currency'], account['balance'])}.\n"
                  "Я скорректирую стартовый остаток, а история останется нетронутой.",
                  back_to="accounts", account_id=account_id)


@router.message(AccountS.rename, TEXT)
async def on_rename(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    emoji, name = split_emoji(message.text, "💳")
    if not valid_name(name):
        await retry(message, state, "Название от 1 до 40 символов.")
        return
    account_id = (await state.get_data())["account_id"]
    await repos.accounts.rename(profile.id, account_id, name, emoji)
    await answer_in_place(message, state, await account_screen(repos, profile, account_id))
    await state.clear()


@router.message(AccountS.set_balance, TEXT)
async def on_set_balance(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    raw = message.text.strip()
    account_id = (await state.get_data())["account_id"]
    account = await repos.accounts.get(profile.id, account_id)
    code = account["currency"] if account else profile.currency
    target = 0 if raw in ("0", "-", "—") else parse_amount(raw, code)
    if target is None:
        await retry(message, state, "Не понял сумму. Пример: 15000 или «0». "
                                    f"Знаков после запятой — не больше {decimals_of(code)}.")
        return
    if account:
        new_initial = target - (account["balance"] - account["initial"])
        await repos.accounts.set_initial(profile.id, account_id, new_initial)
    await answer_in_place(message, state, await account_screen(repos, profile, account_id))
    await state.clear()


@router.callback_query(CB.filter(F.a == "acstar"))
async def on_default(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    account_id = int(callback_data.x or 0)
    if await repos.accounts.get(profile.id, account_id):
        await repos.users.update(profile.id, default_account_id=account_id)
        profile.default_account_id = account_id
    await show(callback, await accounts_screen(repos, profile))


@router.callback_query(CB.filter(F.a == "acdel"))
async def on_archive(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    accounts = await repos.accounts.list(profile.id)
    account = next((a for a in accounts if a["id"] == int(callback_data.x or 0)), None)
    if not account:
        await show(callback, await accounts_screen(repos, profile))
        return
    if len(accounts) == 1:
        await callback.answer("Нужен хотя бы один счёт.", show_alert=True)
        return
    if account["balance"] != 0:
        await callback.answer("На счёте есть деньги. Сначала переведи остаток на другой счёт.",
                              show_alert=True)
        return
    await repos.accounts.archive(profile.id, account["id"])
    if profile.default_account_id == account["id"]:
        profile.default_account_id = None      # default_account() выберет другой счёт
    await show(callback, await accounts_screen(repos, profile))


# ---------------------------------------------------------------- перевод и обмен валют

@router.callback_query(CB.filter(F.a == "acx"))
async def on_transfer_from(callback: CallbackQuery, repos: Repos, profile: Profile):
    accounts = await repos.accounts.list(profile.id)
    if len(accounts) < 2:
        await callback.answer("Для перевода нужно хотя бы два счёта.", show_alert=True)
        return
    buttons = [btn(f"{label(a['emoji'], a['name'])} · {cmoney(a['currency'], a['balance'])}", "acxf", a["id"])
               for a in accounts]
    await show(callback, Screen("🔄 <b>Перевод или обмен валюты</b>\n\nОткуда переводим?",
                                kb(*grid(buttons, 1), [btn("◀️ Назад", "go", "accounts")])))


@router.callback_query(CB.filter(F.a == "acxf"))
async def on_transfer_to(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    source = int(callback_data.x or 0)
    accounts = [a for a in await repos.accounts.list(profile.id) if a["id"] != source]
    buttons = [btn(f"{label(a['emoji'], a['name'])} · {a['currency']}", "acxt", source, a["id"])
               for a in accounts]
    await show(callback, Screen("🔄 <b>Перевод или обмен валюты</b>\n\nКуда переводим?",
                                kb(*grid(buttons, 1), [btn("◀️ Назад", "acx")])))


@router.callback_query(CB.filter(F.a == "acxt"))
async def on_transfer_amount(callback: CallbackQuery, callback_data: CB, state: FSMContext,
                             repos: Repos, profile: Profile):
    src, dst = int(callback_data.x or 0), int(callback_data.y or 0)
    a = await repos.accounts.get(profile.id, src)
    b = await repos.accounts.get(profile.id, dst)
    if not a or not b or src == dst:
        await callback.answer("Не получилось выбрать счета", show_alert=True)
        return
    await ask(callback, state, AccountS.transfer,
              f"🔄 <b>{esc(a['name'])} → {esc(b['name'])}</b>\n\n"
              f"Сколько списываем со счёта (в {CURRENCIES.get(a['currency'], ('', a['currency']))[0]})?",
              back_to="accounts", src=src, dst=dst)


async def finish_transfer(repos: Repos, profile: Profile, src: int, dst: int, amount: int,
                          received: int | None) -> Screen:
    await repos.transactions.add(profile.id, "transfer", amount, src, profile.today,
                                 to_account_id=dst, to_amount=received)
    row = (await repos.transactions.list(profile.id, profile.today, profile.today, kind="transfer",
                                         limit=1))[0]
    sent = cmoney(row["acc_currency"], row["amount"])
    done = (f"{sent} → {cmoney(row['to_currency'], row['to_amount'])}"
            if row["to_amount"] is not None else sent)
    screen = await accounts_screen(repos, profile)
    screen.text = f"✅ Переведено: {done}\n\n" + screen.text
    return screen


@router.message(AccountS.transfer, TEXT)
async def on_transfer(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    data = await state.get_data()
    a = await repos.accounts.get(profile.id, data["src"])
    b = await repos.accounts.get(profile.id, data["dst"])
    if not a or not b:
        await retry(message, state, "Счёт не найден. Начни заново.")
        return
    amount = parse_amount(message.text, a["currency"])
    if amount is None:
        await retry(message, state, "Не понял сумму. Пример: 5000 или 5к. "
                                    f"Знаков после запятой — не больше {decimals_of(a['currency'])}.")
        return
    if a["currency"] == b["currency"]:
        screen = await finish_transfer(repos, profile, a["id"], b["id"], amount, None)
        await answer_in_place(message, state, screen)
        await state.clear()
        return

    # Обмен валют: предлагаем сумму по курсу, но человек вводит то, что реально пришло
    try:
        suggested = await repos.rates.convert(profile.id, amount, a["currency"], b["currency"])
    except RateUnknown:
        suggested = None
    prompt = (f"💱 <b>Обмен {cmoney(a['currency'], amount)} → {esc(b['name'])}</b>\n\n"
              f"Сколько пришло на счёт в {CURRENCIES.get(b['currency'], ('', b['currency']))[0]}? "
              "Напиши сумму" + (" или выбери по курсу." if suggested else "."))
    await state.update_data(t_amount=amount, prompt=prompt)
    await state.set_state(AccountS.received)
    rows = []
    if suggested:
        rows.append([btn(f"≈ {cmoney(b['currency'], suggested)} по курсу", "acxr", suggested)])
    rows.append([btn("✖️ Отмена", "go", "accounts")])
    await answer_in_place(message, state, Screen(prompt, kb(*rows)))


@router.message(AccountS.received, TEXT)
async def on_received(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    data = await state.get_data()
    dst = await repos.accounts.get(profile.id, data["dst"])
    code = dst["currency"] if dst else profile.currency
    received = parse_amount(message.text, code)
    if received is None:
        await retry(message, state, "Не понял сумму. Пример: 90000 или 90к. "
                                    f"Знаков после запятой — не больше {decimals_of(code)}.")
        return
    screen = await finish_transfer(repos, profile, data["src"], data["dst"], data["t_amount"], received)
    await answer_in_place(message, state, screen)
    await state.clear()


@router.callback_query(CB.filter(F.a == "acxr"))
async def on_received_suggested(callback: CallbackQuery, callback_data: CB, state: FSMContext,
                                repos: Repos, profile: Profile):
    data = await state.get_data()
    received = int(callback_data.x or 0)
    if "t_amount" not in data or received <= 0:
        await callback.answer("Эта операция уже закрыта.", show_alert=True)
        return
    screen = await finish_transfer(repos, profile, data["src"], data["dst"], data["t_amount"], received)
    await state.clear()
    await show(callback, screen)
