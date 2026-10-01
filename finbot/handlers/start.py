"""Старт, главное меню, помощь и навигация «Назад»/«Отмена»."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..repos import Repos
from ..repos.users import Profile
from ..services import dates
from ..services.currency_switch import switch_base
from ..services.mascot import with_mascot
from ..services.money import CURRENCIES, FIAT, bar, pct
from ..services.rates import RateUnknown
from ..ui.common import CB, Screen, btn, grid, kb, send, show
from ..ui.format import cmoney, esc, money
from ..ui.nav import build_section, section
from .stats import stats_screen

router = Router(name="start")

HELP = (
    "📖 <b>Как пользоваться</b>\n\n"
    "<b>Быстрая запись одной строкой</b>\n"
    "<code>кофе 250</code> — расход\n"
    "<code>такси 400 вчера</code> — расход за вчера\n"
    "<code>+50000 зарплата</code> — доход\n"
    "<code>1500</code> — просто сумма: бот спросит, что это\n\n"
    "<b>Разделы</b>\n"
    "➕➖ запись дохода и расхода · 📊 статистика и графики\n"
    "🎯 копилки и план распределения денег · 🎚 бюджеты по категориям\n"
    "👛 счета и переводы · 🔁 регулярные платежи · 🤝 долги · 📰 статьи и заметки\n\n"
    "<b>Команды</b>\n"
    "/menu — главное меню · /add — добавить запись · /stats — статистика\n"
    "/cancel — отменить текущее действие"
)


@section("menu")
async def menu_screen(repos: Repos, profile: Profile) -> Screen:
    uid = profile.id
    today = profile.today
    start, end = dates.month_bounds(today.year, today.month)
    capital = await repos.accounts.total(uid)
    saved = await repos.goals.total_saved(uid)
    income, expense = await repos.transactions.totals(uid, start, end)
    net = income - expense

    lines = [
        "💎 <b>Мои финансы</b>",
        "",
        f"🏦 Капитал <b>{money(profile, capital)}</b>",
        f"├ 🎯 В копилках  {money(profile, saved)}",
        f"└ 💵 Свободно  {money(profile, capital - saved)}",
    ]
    by_currency = await repos.accounts.by_currency(uid)
    if len(by_currency) > 1:
        lines.append("💱 " + " · ".join(cmoney(code, value) for code, value in by_currency.items()))
    if saved > capital:
        lines.append("⚠️ В копилках больше, чем на счетах — проверь остатки счетов.")
    lines += [
        "",
        f"📅 <b>{dates.MONTHS_NOM[today.month - 1]}</b>",
        f"▲ Доходы  {money(profile, income)}",
        f"▼ Расходы  {money(profile, expense)}",
        f"━ Итог  <b>{money(profile, net, sign=True)}</b>",
    ]
    if income > 0:
        share = max(0.0, min(net / income, 1.0))
        lines.append(f"{bar(share, 12)} {pct(max(net, 0), income):.0f}% отложено")

    markup = kb(
        [btn("➕ Доход", "add", "income"), btn("➖ Расход", "add", "expense")],
        [btn("📊 Статистика", "go", "stats"), btn("🧾 История", "go", "hist")],
        [btn("🎯 Копилки и план", "go", "goals"), btn("🎚 Бюджеты", "go", "budgets")],
        [btn("👛 Счета", "go", "accounts"), btn("🔁 Регулярные", "go", "recurring")],
        [btn("📰 Статьи", "go", "articles"), btn("🤝 Долги", "go", "debts")],
        [btn("⚙️ Настройки", "go", "settings"), btn("❓ Помощь", "help")],
    )
    return Screen("\n".join(lines), markup)


def _welcome(name: str) -> Screen:
    text = (
        f"👋 <b>Привет, {esc(name) or 'друг'}!</b>\n\n"
        "Я веду твои финансы: доходы с источниками, расходы по категориям, "
        "копилки, бюджеты и красивые графики. Всё управляется кнопками, "
        "а данные видишь только ты.\n\n"
        "Для начала выбери валюту:"
    )
    buttons = [btn(f"{sym} {name}", "cur", code, "start") for code, (sym, name) in FIAT.items()]
    return Screen(text, kb(*grid(buttons, 2)))


@router.message(CommandStart(), StateFilter("*"))
async def cmd_start(message: Message, state: FSMContext, repos: Repos, profile: Profile, is_new: bool):
    await state.clear()
    if is_new:
        screen = with_mascot(_welcome(message.from_user.first_name), profile, "welcome")
        await send(message.bot, message.chat.id, screen)
        return
    await send(message.bot, message.chat.id, await menu_screen(repos, profile))


@router.message(Command("menu"), StateFilter("*"))
async def cmd_menu(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    await state.clear()
    await send(message.bot, message.chat.id, await menu_screen(repos, profile))


@router.message(Command("add"), StateFilter("*"))
async def cmd_add(message: Message, state: FSMContext):
    await state.clear()
    await send(message.bot, message.chat.id, Screen(
        "Что записываем?",
        kb([btn("➖ Расход", "add", "expense"), btn("➕ Доход", "add", "income")],
           [btn("🏠 Меню", "go", "menu")]),
    ))


@router.message(Command("stats"), StateFilter("*"))
async def cmd_stats(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    await state.clear()
    await send(message.bot, message.chat.id, await stats_screen(repos, profile))


@router.message(Command("richtest"), StateFilter("*"))
async def cmd_richtest(message: Message):
    """Проверка «Статей» Telegram: шлёт пробную статью без запасного варианта и сообщает ответ."""
    from aiogram.exceptions import TelegramAPIError

    from ..services.rich import SendRichMessage
    from ..services import article_markup

    sample = "# Проверка\nОбычный абзац.\n- пункт\n> цитата\n| Статья | Сумма |\n| Кофе | 250 |\n---"
    try:
        await message.bot(SendRichMessage(
            chat_id=message.chat.id,
            rich_message=article_markup.to_rich_message("Пробная статья", sample)))
    except TelegramAPIError as error:
        await message.answer(
            "❌ Telegram не принял статью (Rich Message):\n"
            f"<code>{esc(error)}</code>\n\n"
            "Бот продолжит работать: статьи будут приходить обычным текстом.")
        return
    await message.answer("✅ Статьи (Rich Messages) работают.")


@router.message(Command("help"), StateFilter("*"))
async def cmd_help(message: Message):
    await message.answer(HELP, reply_markup=kb([btn("🏠 Меню", "go", "menu")]))


@router.message(Command("cancel"), StateFilter("*"))
async def cmd_cancel(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    had_state = await state.get_state()
    await state.clear()
    if had_state:
        await message.answer("Отменено.")
    await send(message.bot, message.chat.id, await menu_screen(repos, profile))


@router.callback_query(CB.filter(F.a == "go"))
async def on_go(callback: CallbackQuery, callback_data: CB, state: FSMContext,
                repos: Repos, profile: Profile):
    """Универсальный переход в раздел. Сбрасывает незаконченный диалог."""
    await state.clear()
    await show(callback, await build_section(callback_data.x, repos, profile))


@router.callback_query(CB.filter(F.a == "noop"))
async def on_noop(callback: CallbackQuery):
    return None


@router.callback_query(CB.filter(F.a == "help"))
async def on_help(callback: CallbackQuery):
    await show(callback, Screen(HELP, kb([btn("🏠 Меню", "go", "menu")])))


@router.callback_query(CB.filter(F.a == "cur"))
async def on_currency(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    code = callback_data.x
    if code not in FIAT:                      # основной может быть только обычная валюта
        return
    has_data = await repos.db.scalar(
        "SELECT COUNT(*) FROM transactions WHERE user_id = ?", (profile.id,))
    if callback_data.y == "start" and not has_data:
        # Приветствие нового пользователя: данных нет, просто задаём валюту всем счетам
        await repos.users.update(profile.id, currency=code)
        await repos.accounts.set_all_currency(profile.id, code)
        profile.currency = code
    else:
        try:
            await switch_base(repos, profile, code)
        except RateUnknown as error:
            await callback.answer(
                f"Нет курса {error.src} → {error.dst}. Задай его в «Валюты и курсы».", show_alert=True)
            return
    if callback_data.y == "start":
        tip = (
            f"✅ Валюта: {CURRENCIES[code][0]} {CURRENCIES[code][1]}\n\n"
            "Попробуй сразу: отправь сообщение <code>кофе 250</code> — я запишу расход.\n"
            "Остальное найдёшь в меню."
        )
        screen = await menu_screen(repos, profile)
        screen.text = tip + "\n\n" + screen.text
        await show(callback, screen)
    else:
        await show(callback, await build_section("currencies", repos, profile))
