"""Валюты и курсы: основная валюта, автоматические и свои курсы."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..repos import Repos
from ..repos.users import Profile
from ..services.money import CURRENCIES, FIAT, NBSP
from ..states import CurS
from ..ui.common import CB, TEXT, Screen, btn, grid, kb, show
from ..ui.nav import section
from ..ui.prompts import answer_in_place, ask, retry
from .accounts import ask_initial

router = Router(name="currencies")


def _rate_text(value: Decimal) -> str:
    if value < 1:
        return f"{value:.4f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{value:,.2f}".replace(",", NBSP).replace(".", ",")


async def currencies_screen(repos: Repos, profile: Profile) -> Screen:
    uid = profile.id
    base = profile.currency
    used = {a["currency"] for a in await repos.accounts.list(uid)} | set(await repos.rates.overrides(uid))
    used.discard(base)
    symbol, name = CURRENCIES.get(base, ("¤", base))
    lines = ["💱 <b>Валюты и курсы</b>", "",
             f"Основная валюта: <b>{symbol} {name}</b>",
             "В ней считаются итоги, статистика, бюджеты, копилки и долги. "
             "Счета могут быть в любых валютах — суммы пересчитываются по курсу на день записи.", ""]
    buttons = []
    own = await repos.rates.overrides(uid)
    for code in sorted(used):
        rate, source = await repos.rates.describe(uid, code)
        sym = CURRENCIES.get(code, ("¤", code))[0]
        shown = f"1 {sym} = {_rate_text(rate)} {profile.symbol}" if rate else f"{sym}: курс не задан"
        lines.append(f"{shown} · {source or '—'}")
        buttons.append(btn(f"✏️ Курс {code}", "curo", code))
        if code in own:
            buttons.append(btn(f"↩️ Авто {code}", "cura", code))
    if not used:
        lines.append("Пока все счета в основной валюте. Создай счёт в другой валюте "
                     "(«Счета → Новый счёт») — и курс появится здесь.")
    updated = await repos.rates.last_update()
    if updated:
        local = updated.astimezone(profile.tz).strftime("%d.%m %H:%M")
        lines += ["", f"<i>Курсы ЦБ РФ обновлены {local}.</i>"]
    else:
        lines += ["", "<i>Автоматические курсы ещё не загружены — можно задать свои.</i>"]
    markup = kb(
        [btn("🔄 Обновить курсы", "curref"), btn("💱 Сменить основную", "curbase")],
        *grid(buttons, 2),
        [btn("◀️ Назад", "go", "settings")],
    )
    return Screen("\n".join(lines), markup)


@section("currencies")
async def currencies_section(repos: Repos, profile: Profile) -> Screen:
    return await currencies_screen(repos, profile)


@router.callback_query(CB.filter(F.a == "curref"))
async def on_refresh(callback: CallbackQuery, repos: Repos, profile: Profile):
    ok = await repos.rates.refresh()
    await callback.answer("Курсы обновлены ✅" if ok else "Не удалось получить курсы. Попробуй позже.",
                          show_alert=not ok)
    await show(callback, await currencies_screen(repos, profile))


# ---------------------------------------------------------------- смена основной валюты

@router.callback_query(CB.filter(F.a == "curbase"))
async def on_base_picker(callback: CallbackQuery, profile: Profile):
    buttons = [btn(("✅ " if code == profile.currency else "") + f"{sym} {name}", "curbc", code)
               for code, (sym, name) in FIAT.items()]
    await show(callback, Screen("💱 <b>Основная валюта</b>\n\nВыбери новую. Криптовалюта основной быть не может: "
                                "в ней нет копеек для бюджетов и копилок — но счета в крипте вести можно.",
                                kb(*grid(buttons, 2), [btn("◀️ Назад", "go", "currencies")])))


@router.callback_query(CB.filter(F.a == "curbc"))
async def on_base_confirm(callback: CallbackQuery, callback_data: CB, profile: Profile):
    code = callback_data.x
    if code not in FIAT or code == profile.currency:
        await show(callback, Screen("Эта валюта уже основная или не может быть основной.", kb([btn("◀️ Назад", "go", "currencies")])))
        return
    sym, name = CURRENCIES[code]
    await show(callback, Screen(
        f"💱 <b>Сменить основную валюту на {sym} {name}?</b>\n\n"
        "Статистика, цели копилок, лимиты бюджетов и долги пересчитаются по <b>сегодняшнему курсу</b>. "
        "Счета останутся в своих валютах. Остатки на счетах и записи в их валютах не изменятся.",
        kb([btn("✅ Да, сменить", "cur", code, "settings")], [btn("◀️ Нет", "go", "currencies")])))


# ---------------------------------------------------------------- свой курс

@router.callback_query(CB.filter(F.a == "curo"))
async def on_own_rate(callback: CallbackQuery, callback_data: CB, state: FSMContext, profile: Profile):
    code = callback_data.x
    if code not in CURRENCIES or code == profile.currency:
        return
    await ask(callback, state, CurS.rate,
              f"✏️ <b>Свой курс {code}</b>\n\nСколько {profile.symbol} стоит 1 {CURRENCIES[code][0]}? "
              "Например <code>92,5</code>. Он будет главнее автоматического.",
              back_to="currencies", rate_for=code, then="currencies")


@router.message(CurS.rate, TEXT)
async def on_rate_text(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    try:
        value = Decimal(message.text.strip().replace(",", ".").replace(" ", ""))
    except InvalidOperation:
        value = Decimal(0)
    if not Decimal("0.000001") <= value <= Decimal("1000000000000"):
        await retry(message, state, "Нужно положительное число, например 92,5.")
        return
    data = await state.get_data()
    code = data["rate_for"]
    await repos.rates.set_override(profile.id, code, value)
    if data.get("then") == "account":
        await ask_initial(state, code, data["a_name"], message=message)
        return
    await answer_in_place(message, state, await currencies_screen(repos, profile))
    await state.clear()


@router.callback_query(CB.filter(F.a == "cura"))
async def on_auto_rate(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    code = callback_data.x
    if await repos.rates.auto_rate(code, profile.currency) is None:
        # без автоматического курса счета в этой валюте потеряли бы пересчёт — свой оставляем
        await callback.answer("Автоматического курса для этой валюты нет — оставляю твой.",
                              show_alert=True)
    else:
        await repos.rates.clear_override(profile.id, code)
    await show(callback, await currencies_screen(repos, profile))
