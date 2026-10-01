"""Запись дохода и расхода: сумма → категория → (источник) → карточка → сохранить."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..repos import Repos
from ..repos.users import Profile
from ..services import dates
from ..services.alerts import budget_alerts
from ..services.mascot import expense_pool, income_key, with_mascot
from ..services.money import CURRENCIES, decimals_of, format_major, minor_of, parse_major
from ..services.parser import split_currency
from ..states import Add
from ..ui.common import CB, TEXT, Screen, btn, drop, grid, kb, show
from ..services.rates import RateUnknown
from ..ui.format import (
    cmoney, esc, kind_icon, kind_title, label, money, split_emoji, tx_amount, valid_name,
)
from ..ui.nav import build_section
from ..ui.prompts import answer_in_place, ask, retry

router = Router(name="add")


# ---------------------------------------------------------------- черновик

async def new_draft(repos: Repos, profile: Profile, kind: str, value: Decimal | None = None,
                    day: date | None = None, account: dict | None = None, **extra) -> dict:
    """Черновик записи.

    Сумма живёт в двух видах: "major" — как набрал пользователь (точное число), "amount" — в
    минимальных единицах валюты выбранного счёта. Второе пересчитывается при смене валюты:
    у BTC 8 знаков после запятой, у рубля 2, поэтому одно число в разных валютах хранится по-разному.
    """
    account = account or await repos.default_account(profile)
    draft = {
        "kind": kind, "major": str(value) if value is not None else None,
        "amount": minor_of(value, account["currency"]) if value is not None else None,
        "category_id": None, "source_id": None,
        "account_id": account["id"], "currency": account["currency"],
        "day": (day or profile.today).isoformat(), "note": "",
    }
    draft.update(extra)
    return draft


def apply_account(draft: dict, account) -> bool:
    """Переносит черновик на счёт (и в его валюту). False — сумма не помещается в валюту
    (например, 0,00015 в рублях): тогда черновик не меняется."""
    minor = minor_of(Decimal(draft["major"]), account["currency"])
    if minor is None:
        return False
    draft["account_id"], draft["currency"], draft["amount"] = account["id"], account["currency"], minor
    return True


def precision_text(code: str) -> str:
    digits = decimals_of(code)
    return f"В {code} сумма может иметь не больше {digits} знаков после запятой."


async def pick_account(repos: Repos, profile: Profile, currency: str):
    """Счёт для записи в названной валюте: основной, если подходит, иначе первый в этой валюте.

    Если счёта в такой валюте нет — заводим (при условии, что курс известен).
    Возвращает (счёт | None, создан_ли_новый).
    """
    accounts = await repos.accounts.list(profile.id)
    matching = [a for a in accounts if a["currency"] == currency]
    default = next((a for a in matching if a["id"] == profile.default_account_id), None)
    if default or matching:
        return default or matching[0], False
    if currency not in CURRENCIES or not await repos.rates.has_rate(profile.id, currency):
        return None, False
    name = CURRENCIES[currency][1]
    account_id = await repos.accounts.add(profile.id, name, "💱", 0, currency)
    return await repos.accounts.get(profile.id, account_id), True


def draft_money(draft: dict) -> str:
    if draft["amount"]:
        return cmoney(draft["currency"], draft["amount"])
    return format_major(Decimal(draft["major"]))        # валюта ещё не выбрана — показываем как набрали


async def get_draft(state: FSMContext) -> dict | None:
    data = await state.get_data()
    return data.get("draft")


async def put_draft(state: FSMContext, draft: dict) -> None:
    await state.update_data(draft=draft)


async def _stale(callback: CallbackQuery, state: FSMContext, repos: Repos, profile: Profile):
    """Черновика нет (бот перезапускался) — не оставляем пользователя с мёртвыми кнопками."""
    await state.clear()
    await callback.answer("Эта запись уже закрыта. Начни заново.", show_alert=True)
    await show(callback, await build_section("menu", repos, profile))


# ---------------------------------------------------------------- экраны

async def category_picker(repos: Repos, profile: Profile, draft: dict) -> Screen:
    cats = await repos.categories.list(profile.id, draft["kind"])
    buttons = [btn(label(c["emoji"], c["name"]), "adc", c["id"]) for c in cats]
    text = (f"{kind_icon(draft['kind'])} <b>{kind_title(draft['kind'])} "
            f"{draft_money(draft)}</b>\n\n"
            + ("Откуда пришли деньги? Выбери <b>вид дохода</b>:" if draft["kind"] == "income"
               else "Выбери <b>категорию</b>:"))
    markup = kb(*grid(buttons, 2), [btn("➕ Новая категория", "adcn")],
                [btn("✖️ Отмена", "go", "menu")])
    return Screen(text, markup)


async def source_picker(repos: Repos, profile: Profile, draft: dict) -> Screen:
    sources = await repos.sources.list(profile.id)
    buttons = [btn(label(s["emoji"], s["name"]), "ads", s["id"]) for s in sources]
    text = (f"➕ <b>Доход {draft_money(draft)}</b>\n\n"
            "Из какого <b>источника</b>? Это работодатель, клиент, платформа — "
            "так потом видно, что приносит больше всего.")
    markup = kb(*grid(buttons, 2), [btn("➕ Новый источник", "adsn"), btn("⏭ Без источника", "ads", 0)],
                [btn("✖️ Отмена", "go", "menu")])
    return Screen(text, markup)


async def card_screen(repos: Repos, profile: Profile, draft: dict) -> Screen:
    uid = profile.id
    category = await repos.categories.get(uid, draft["category_id"]) if draft["category_id"] else None
    source = await repos.sources.get(uid, draft["source_id"]) if draft["source_id"] else None
    account = await repos.accounts.get(uid, draft["account_id"])
    day = date.fromisoformat(draft["day"])
    income = draft["kind"] == "income"

    approx = ""
    if draft["currency"] != profile.currency:
        try:
            base = await repos.rates.to_base(uid, draft["amount"], draft["currency"])
            approx = f"  (≈{money(profile, base)})"
        except RateUnknown:
            approx = "  (курс не задан)"
    lines = [f"{kind_icon(draft['kind'])} <b>{kind_title(draft['kind'])} · "
             f"{draft_money(draft)}</b>{approx}", ""]
    lines.append(f"🏷 {'Вид' if income else 'Категория'}: "
                 f"{esc(label(category['emoji'], category['name'])) if category else '—'}")
    if income:
        lines.append(f"🧾 Источник: {esc(label(source['emoji'], source['name'])) if source else '—'}")
    lines.append(f"👛 Счёт: {esc(label(account['emoji'], account['name'])) if account else '—'}")
    lines.append(f"📅 Дата: {dates.fmt_day(day, profile.today)}")
    lines.append(f"📝 Заметка: {esc(draft['note']) if draft['note'] else '—'}")
    lines += ["", "<i>Можно просто написать текст — он станет заметкой.</i>"]

    top = [btn("🏷 Вид" if income else "🏷 Категория", "adcp")]
    if income:
        top.append(btn("🧾 Источник", "adsp"))
    markup = kb(
        top,
        [btn("👛 Счёт", "adap"), btn("💱 Валюта", "adcurp")],
        [btn("📅 Дата", "adtp"), btn("📝 Заметка", "adnote")],
        [btn("✅ Сохранить", "adsave")],
        [btn("✖️ Отмена", "go", "menu")],
    )
    return Screen("\n".join(lines), markup)


async def result_screen(repos: Repos, profile: Profile, tx_id: int, notes: list[str]) -> Screen:
    row = await repos.transactions.get(profile.id, tx_id)
    income = row["kind"] == "income"
    lines = ["✅ <b>Записано</b>", "",
             f"{kind_icon(row['kind'])} <b>{kind_title(row['kind'])} · {tx_amount(row, profile)}</b>",
             f"{esc(label(row['cat_emoji'], row['cat_name']))}"
             + (f" · 🧾 {esc(row['src_name'])}" if income and row["src_name"] else ""),
             f"👛 {esc(row['acc_name'])} · 📅 {dates.fmt_day(date.fromisoformat(row['day']), profile.today)}"]
    if row["note"]:
        lines.append(f"📝 {esc(row['note'])}")
    if notes:
        lines += [""] + notes

    rows = []
    if income:
        plan = [g for g in await repos.goals.list(profile.id) if g["percent"] > 0]
        if plan:
            rows.append([btn("💰 Распределить по плану", "dist", tx_id)])
    rows.append([btn("↩️ Отменить", "txundo", tx_id), btn("✏️ Изменить", "tx", tx_id)])
    rows.append([btn("➕ Ещё доход" if income else "➕ Ещё расход", "add", row["kind"]),
                 btn("🏠 Меню", "go", "menu")])
    screen = Screen("\n".join(lines), kb(*rows))

    # Хомяк: к каждому доходу, а к расходу — когда есть подходящая сцена или превышен лимит
    if income:
        key = income_key(row["cat_name"], bool(row["cat_passive"]))
        return with_mascot(screen, profile, key)
    if any("🚨" in note for note in notes):
        return with_mascot(screen, profile, "overspend")
    pool = expense_pool(row["cat_name"])
    return with_mascot(screen, profile, pool[0], pool) if pool else screen


async def save_draft(repos: Repos, profile: Profile, draft: dict) -> Screen:
    """Сохраняет черновик и собирает экран результата с предупреждениями о лимитах."""
    day = date.fromisoformat(draft["day"])
    tx_id = await repos.transactions.add(
        profile.id, draft["kind"], draft["amount"], draft["account_id"], day,
        category_id=draft["category_id"], source_id=draft["source_id"] or None,
        note=draft["note"],
    )
    notes = []
    if draft["kind"] == "expense":
        # лимиты бюджета считаются в основной валюте
        saved = await repos.transactions.get(profile.id, tx_id)
        notes = await budget_alerts(repos, profile, draft["category_id"], day, saved["base_amount"])
    return await result_screen(repos, profile, tx_id, notes)


# ---------------------------------------------------------------- вход

@router.callback_query(CB.filter(F.a == "add"))
async def on_add(callback: CallbackQuery, callback_data: CB, state: FSMContext,
                 repos: Repos, profile: Profile):
    kind = callback_data.x
    if kind not in ("income", "expense"):
        return
    await state.clear()
    try:
        prefilled = Decimal(callback_data.y) if callback_data.y else None
    except InvalidOperation:
        prefilled = None
    draft = await new_draft(repos, profile, kind, value=prefilled)
    await state.update_data(draft=draft, msg=callback.message.message_id)
    if prefilled:            # сумма пришла из быстрого ввода — дальше валюта
        await state.set_state(Add.card)
        await show(callback, await currency_picker(repos, profile, draft, first=True))
        return
    title = "доход" if kind == "income" else "расход"
    await ask(
        callback, state, Add.amount,
        f"{kind_icon(kind)} <b>Новый {title}</b>\n\nВведи сумму, например "
        f"<code>1500</code>, <code>2к</code> или <code>1 250,50</code>.\n"
        "Валюту выберешь следующим шагом — или сразу допиши её: <code>20$</code>, <code>500 евро</code>.",
        back_to="menu",
    )


@router.message(Add.amount, TEXT)
async def on_amount(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    text, written_currency = split_currency(message.text)
    value = parse_major(text)
    if value is None:
        await retry(message, state, "Не понял сумму. Пример: 1500, 2к или 20$.")
        return
    draft = await get_draft(state)
    if draft is None:
        await drop(message)
        return
    draft["major"] = str(value)
    draft["amount"] = minor_of(value, draft["currency"])      # в валюте счёта по умолчанию, если она подходит
    if written_currency:                 # «20$» — валюта названа, отдельный шаг не нужен
        account, _created = await pick_account(repos, profile, written_currency)
        if account is None:
            await retry(message, state, f"Не знаю курс {written_currency}. Задай его в «Настройки → "
                                        "Валюты и курсы» или выбери другую валюту.")
            return
        if not apply_account(draft, account):
            await retry(message, state, precision_text(written_currency))
            return
        await put_draft(state, draft)
        await state.set_state(Add.card)
        await answer_in_place(message, state, await category_picker(repos, profile, draft))
        return
    await state.set_state(Add.card)
    await put_draft(state, draft)
    await answer_in_place(message, state, await currency_picker(repos, profile, draft, first=True))


# ---------------------------------------------------------------- категория и источник

@router.callback_query(CB.filter(F.a == "adc"))
async def on_choose_category(callback: CallbackQuery, callback_data: CB, state: FSMContext,
                             repos: Repos, profile: Profile):
    draft = await get_draft(state)
    if draft is None:
        return await _stale(callback, state, repos, profile)
    category = await repos.categories.get(profile.id, int(callback_data.x or 0))
    if not category or category["kind"] != draft["kind"] or category["archived"]:
        await callback.answer("Такой категории нет", show_alert=True)
        return
    draft["category_id"] = category["id"]
    await put_draft(state, draft)
    await state.set_state(Add.card)
    if draft["kind"] == "income" and draft["source_id"] is None:
        await show(callback, await source_picker(repos, profile, draft))
    else:
        await show(callback, await card_screen(repos, profile, draft))


@router.callback_query(CB.filter(F.a == "adcp"))
async def on_open_category_picker(callback: CallbackQuery, state: FSMContext,
                                  repos: Repos, profile: Profile):
    draft = await get_draft(state)
    if draft is None:
        return await _stale(callback, state, repos, profile)
    await show(callback, await category_picker(repos, profile, draft))


@router.callback_query(CB.filter(F.a == "ads"))
async def on_choose_source(callback: CallbackQuery, callback_data: CB, state: FSMContext,
                           repos: Repos, profile: Profile):
    draft = await get_draft(state)
    if draft is None:
        return await _stale(callback, state, repos, profile)
    source_id = int(callback_data.x or 0)
    if source_id and not await repos.sources.get(profile.id, source_id):
        await callback.answer("Такого источника нет", show_alert=True)
        return
    draft["source_id"] = source_id        # 0 — осознанно «без источника»
    await put_draft(state, draft)
    await show(callback, await card_screen(repos, profile, draft))


@router.callback_query(CB.filter(F.a == "adsp"))
async def on_open_source_picker(callback: CallbackQuery, state: FSMContext,
                                repos: Repos, profile: Profile):
    draft = await get_draft(state)
    if draft is None:
        return await _stale(callback, state, repos, profile)
    await show(callback, await source_picker(repos, profile, draft))


@router.callback_query(CB.filter(F.a == "adcn"))
async def on_new_category(callback: CallbackQuery, state: FSMContext,
                          repos: Repos, profile: Profile):
    draft = await get_draft(state)
    if draft is None:
        return await _stale(callback, state, repos, profile)
    await ask(callback, state, Add.new_category,
              "🏷 <b>Новая категория</b>\n\nНапиши название, можно с эмодзи в начале: "
              "<code>🎣 Рыбалка</code>", back_to="menu")


@router.message(Add.new_category, TEXT)
async def on_new_category_name(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    draft = await get_draft(state)
    if draft is None:
        await drop(message)
        return
    emoji, name = split_emoji(message.text, "🏷")
    if not valid_name(name):
        await retry(message, state, "Название от 1 до 40 символов.")
        return
    draft["category_id"] = await repos.categories.add(profile.id, draft["kind"], name, emoji)
    await put_draft(state, draft)
    await state.set_state(Add.card)
    if draft["kind"] == "income" and draft["source_id"] is None:
        screen = await source_picker(repos, profile, draft)
    else:
        screen = await card_screen(repos, profile, draft)
    await answer_in_place(message, state, screen)


@router.callback_query(CB.filter(F.a == "adsn"))
async def on_new_source(callback: CallbackQuery, state: FSMContext, repos: Repos, profile: Profile):
    draft = await get_draft(state)
    if draft is None:
        return await _stale(callback, state, repos, profile)
    await ask(callback, state, Add.new_source,
              "🧾 <b>Новый источник дохода</b>\n\nНапиши название: работодатель, клиент, "
              "площадка. Например <code>🏢 ООО Ромашка</code>", back_to="menu")


@router.message(Add.new_source, TEXT)
async def on_new_source_name(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    draft = await get_draft(state)
    if draft is None:
        await drop(message)
        return
    emoji, name = split_emoji(message.text, "🏷")
    if not valid_name(name):
        await retry(message, state, "Название от 1 до 40 символов.")
        return
    draft["source_id"] = await repos.sources.add(profile.id, name, emoji)
    await put_draft(state, draft)
    await state.set_state(Add.card)
    await answer_in_place(message, state, await card_screen(repos, profile, draft))


# ---------------------------------------------------------------- счёт, дата, заметка

@router.callback_query(CB.filter(F.a == "adap"))
async def on_open_account_picker(callback: CallbackQuery, state: FSMContext,
                                 repos: Repos, profile: Profile):
    draft = await get_draft(state)
    if draft is None:
        return await _stale(callback, state, repos, profile)
    accounts = await repos.accounts.list(profile.id)
    buttons = [btn(f"{label(a['emoji'], a['name'])} · {cmoney(a['currency'], a['balance'])}",
                   "ada", a["id"]) for a in accounts]
    await show(callback, Screen("👛 <b>С какого счёта?</b>\n<i>Сумма записывается в валюте счёта.</i>",
                                kb(*grid(buttons, 1), [btn("◀️ Назад", "adback")])))


async def currency_picker(repos: Repos, profile: Profile, draft: dict, first: bool = False) -> Screen:
    """Выбор валюты записи. Сначала валюты, в которых у пользователя есть счета.

    first=True — шаг сразу после ввода суммы (дальше категория), иначе — правка из карточки.
    """
    own = []
    for account in await repos.accounts.list(profile.id):
        if account["currency"] not in own:
            own.append(account["currency"])
    codes = own + [code for code in CURRENCIES if code not in own]
    step = "1" if first else ""
    buttons = [btn(("✅ " if code == draft["currency"] else "") + f"{CURRENCIES[code][0]} {CURRENCIES[code][1]}",
                   "adcur", code, step) for code in codes]
    note = ("<i>Запись пойдёт со счёта в этой валюте. Если такого счёта ещё нет — заведу сам "
            "(он появится в «Счетах»).</i>")
    shown = format_major(Decimal(draft["major"]))
    footer = [btn("✖️ Отмена", "go", "menu")] if first else [btn("◀️ Назад", "adback")]
    return Screen(f"💱 <b>В какой валюте {shown}?</b>\n{note}", kb(*grid(buttons, 2), footer))


@router.callback_query(CB.filter(F.a == "adcurp"))
async def on_open_currency_picker(callback: CallbackQuery, state: FSMContext,
                                  repos: Repos, profile: Profile):
    draft = await get_draft(state)
    if draft is None:
        return await _stale(callback, state, repos, profile)
    await show(callback, await currency_picker(repos, profile, draft))


@router.callback_query(CB.filter(F.a == "adcur"))
async def on_choose_currency(callback: CallbackQuery, callback_data: CB, state: FSMContext,
                             repos: Repos, profile: Profile):
    draft = await get_draft(state)
    if draft is None:
        return await _stale(callback, state, repos, profile)
    code = callback_data.x
    if code not in CURRENCIES:
        return
    account, _created = await pick_account(repos, profile, code)
    if account is None:
        await callback.answer(
            f"Нет курса {code}. Задай его в «Настройки → Валюты и курсы».", show_alert=True)
        return
    if not apply_account(draft, account):
        await callback.answer(precision_text(code), show_alert=True)
        return
    await put_draft(state, draft)
    if callback_data.y == "1":
        await show(callback, await category_picker(repos, profile, draft))
    else:
        await show(callback, await card_screen(repos, profile, draft))


@router.callback_query(CB.filter(F.a == "ada"))
async def on_choose_account(callback: CallbackQuery, callback_data: CB, state: FSMContext,
                            repos: Repos, profile: Profile):
    draft = await get_draft(state)
    if draft is None:
        return await _stale(callback, state, repos, profile)
    account = await repos.accounts.get(profile.id, int(callback_data.x or 0))
    if not account:
        await callback.answer("Такого счёта нет", show_alert=True)
        return
    if not apply_account(draft, account):      # сумма теперь понимается в валюте этого счёта
        await callback.answer(precision_text(account["currency"]), show_alert=True)
        return
    await put_draft(state, draft)
    await show(callback, await card_screen(repos, profile, draft))


@router.callback_query(CB.filter(F.a == "adback"))
async def on_back_to_card(callback: CallbackQuery, state: FSMContext, repos: Repos, profile: Profile):
    draft = await get_draft(state)
    if draft is None:
        return await _stale(callback, state, repos, profile)
    await state.set_state(Add.card)
    await show(callback, await card_screen(repos, profile, draft))


@router.callback_query(CB.filter(F.a == "adtp"))
async def on_open_date_picker(callback: CallbackQuery, state: FSMContext, repos: Repos, profile: Profile):
    draft = await get_draft(state)
    if draft is None:
        return await _stale(callback, state, repos, profile)
    markup = kb(
        [btn("Сегодня", "adt", "0"), btn("Вчера", "adt", "1"), btn("Позавчера", "adt", "2")],
        [btn("📆 Другая дата", "adt", "custom")],
        [btn("◀️ Назад", "adback")],
    )
    await show(callback, Screen("📅 <b>За какое число запись?</b>", markup))


@router.callback_query(CB.filter(F.a == "adt"))
async def on_choose_date(callback: CallbackQuery, callback_data: CB, state: FSMContext,
                         repos: Repos, profile: Profile):
    draft = await get_draft(state)
    if draft is None:
        return await _stale(callback, state, repos, profile)
    if callback_data.x == "custom":
        await ask(callback, state, Add.date,
                  "📆 Напиши дату: <code>15.09</code>, <code>15.09.2026</code> или «вчера».",
                  back_to="menu")
        return
    from datetime import timedelta
    offset = int(callback_data.x or 0)
    draft["day"] = (profile.today - timedelta(days=offset)).isoformat()
    await put_draft(state, draft)
    await show(callback, await card_screen(repos, profile, draft))


@router.message(Add.date, TEXT)
async def on_date_text(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    parsed = dates.parse_user_date(message.text, profile.today)
    if parsed is None:
        await retry(message, state, "Не разобрал дату. Пример: 15.09 или «вчера». Будущее нельзя.")
        return
    draft = await get_draft(state)
    if draft is None:
        await drop(message)
        return
    draft["day"] = parsed.isoformat()
    await put_draft(state, draft)
    await state.set_state(Add.card)
    await answer_in_place(message, state, await card_screen(repos, profile, draft))


@router.callback_query(CB.filter(F.a == "adnote"))
async def on_ask_note(callback: CallbackQuery, state: FSMContext, repos: Repos, profile: Profile):
    draft = await get_draft(state)
    if draft is None:
        return await _stale(callback, state, repos, profile)
    await ask(callback, state, Add.note, "📝 Напиши заметку к записи (до 300 символов).", back_to="menu")


@router.message(Add.note, TEXT)
@router.message(Add.card, TEXT)
async def on_note_text(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    draft = await get_draft(state)
    if draft is None:
        await drop(message)
        return
    draft["note"] = message.text.strip()[:300]
    await put_draft(state, draft)
    await state.set_state(Add.card)
    if draft.get("category_id") is None:
        await answer_in_place(message, state, await category_picker(repos, profile, draft))
    else:
        await answer_in_place(message, state, await card_screen(repos, profile, draft))


# ---------------------------------------------------------------- сохранить / отменить

@router.callback_query(CB.filter(F.a == "adsave"))
async def on_save(callback: CallbackQuery, state: FSMContext, repos: Repos, profile: Profile):
    draft = await get_draft(state)
    if draft is None:
        return await _stale(callback, state, repos, profile)
    if not draft.get("category_id") or not draft.get("amount"):
        await callback.answer("Сначала выбери валюту и категорию", show_alert=True)
        return
    await state.clear()                    # до записи: двойной тап не создаст дубль
    screen = await save_draft(repos, profile, draft)
    await show(callback, screen)


@router.callback_query(CB.filter(F.a == "txundo"))
async def on_undo(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    deleted = await repos.transactions.delete(profile.id, int(callback_data.x or 0))
    screen = await build_section("menu", repos, profile)
    if deleted:
        screen.text = "↩️ Запись отменена.\n\n" + screen.text
    await show(callback, screen)
