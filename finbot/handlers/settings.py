"""Настройки: валюта, часовой пояс, напоминания, экспорт, удаление данных."""
from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery

from ..defaults import TIMEZONES
from ..repos import Repos
from ..repos.users import Profile, load_zone
from ..services.exporter import build_csv
from ..services.money import CURRENCIES
from ..ui.common import CB, Screen, btn, grid, kb, show
from ..ui.format import esc
from ..ui.nav import section

router = Router(name="settings")

REMINDER_HOURS = [None, 12, 18, 20, 21, 22]


def _tz_label(name: str) -> str:
    return next((title for key, title in TIMEZONES if key == name), name)


@section("settings")
async def settings_screen(repos: Repos, profile: Profile) -> Screen:
    symbol, currency_name = CURRENCIES.get(profile.currency, ("¤", profile.currency))
    reminder = f"каждый день в {profile.reminder_hour}:00" if profile.reminder_hour is not None else "выключено"
    local = datetime.now(profile.tz).strftime("%H:%M")
    text = (
        "⚙️ <b>Настройки</b>\n\n"
        f"💱 Основная валюта: <b>{symbol} {currency_name}</b>\n"
        f"🕒 Часовой пояс: <b>{esc(_tz_label(profile.tz_name))}</b> (сейчас {local})\n"
        f"⏰ Напоминание записать траты: <b>{reminder}</b>\n"
        f"📬 Недельный дайджест: <b>{'включён' if profile.digest else 'выключен'}</b>\n"
        f"🐹 Картинки с хомяком: <b>{'включены' if profile.mascot else 'выключены'}</b>"
    )
    markup = kb(
        [btn("💱 Валюты и курсы", "go", "currencies"), btn("🕒 Часовой пояс", "settz")],
        [btn("⏰ Напоминание", "setrem"), btn("📬 Дайджест", "setdig")],
        [btn("🐹 Хомяк: " + ("выключить" if profile.mascot else "включить"), "setmas")],
        [btn("🗂 Справочники", "go", "dicts"), btn("📤 Экспорт CSV", "export")],
        [btn("❓ Помощь", "help"), btn("🗑 Удалить мои данные", "wipe")],
        [btn("🏠 Меню", "go", "menu")],
    )
    return Screen(text, markup)


@router.callback_query(CB.filter(F.a == "settz"))
async def on_tz(callback: CallbackQuery):
    buttons = [btn(title, "settzs", i) for i, (_, title) in enumerate(TIMEZONES)]
    await show(callback, Screen(
        "🕒 <b>Часовой пояс</b>\n\nОт него зависят «сегодня», регулярные операции и напоминания.",
        kb(*grid(buttons, 2), [btn("◀️ Назад", "go", "settings")])))


@router.callback_query(CB.filter(F.a == "settzs"))
async def on_tz_set(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    index = int(callback_data.x or 0)
    if 0 <= index < len(TIMEZONES):
        name = TIMEZONES[index][0]
        if getattr(load_zone(name), "key", None) == name:
            await repos.users.update(profile.id, tz=name)
            profile.tz_name = name
    await show(callback, await settings_screen(repos, profile))


@router.callback_query(CB.filter(F.a == "setrem"))
async def on_reminder(callback: CallbackQuery, repos: Repos, profile: Profile):
    buttons = [btn(("✅ " if hour == profile.reminder_hour else "") +
                   ("Выключить" if hour is None else f"{hour}:00"), "setrems", hour if hour is not None else "off")
               for hour in REMINDER_HOURS]
    await show(callback, Screen(
        "⏰ <b>Напоминание</b>\n\nВ выбранный час напомню записать траты — "
        "если за день не было ни одной записи.",
        kb(*grid(buttons, 3), [btn("◀️ Назад", "go", "settings")])))


@router.callback_query(CB.filter(F.a == "setrems"))
async def on_reminder_set(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    hour = None if callback_data.x == "off" else int(callback_data.x or 0)
    if hour is None or hour in REMINDER_HOURS:
        await repos.users.update(profile.id, reminder_hour=hour)
        profile.reminder_hour = hour
    await show(callback, await settings_screen(repos, profile))


@router.callback_query(CB.filter(F.a == "setdig"))
async def on_digest(callback: CallbackQuery, repos: Repos, profile: Profile):
    profile.digest = not profile.digest
    await repos.users.update(profile.id, digest=int(profile.digest))
    await show(callback, await settings_screen(repos, profile))


@router.callback_query(CB.filter(F.a == "setmas"))
async def on_mascot(callback: CallbackQuery, repos: Repos, profile: Profile):
    profile.mascot = not profile.mascot
    await repos.users.update(profile.id, mascot=int(profile.mascot))
    await show(callback, await settings_screen(repos, profile))


@router.callback_query(CB.filter(F.a == "export"))
async def on_export(callback: CallbackQuery, repos: Repos, profile: Profile):
    data = await build_csv(repos, profile)
    name = f"finance_{profile.today.isoformat()}.csv"
    await callback.bot.send_document(
        callback.from_user.id, BufferedInputFile(data, name),
        caption="📤 Все твои операции. Открывается в Excel и Google Таблицах.")


@router.callback_query(CB.filter(F.a == "wipe"))
async def on_wipe_ask(callback: CallbackQuery):
    await show(callback, Screen(
        "🗑 <b>Удалить все мои данные?</b>\n\nСчета, операции, копилки, бюджеты и статьи "
        "будут стёрты <b>безвозвратно</b>. Совет: сначала сделай экспорт CSV.",
        kb([btn("📤 Сначала экспорт", "export")],
           [btn("🗑 Да, удалить всё", "wipey")], [btn("◀️ Нет, оставить", "go", "settings")])))


@router.callback_query(CB.filter(F.a == "wipey"))
async def on_wipe(callback: CallbackQuery, state: FSMContext, repos: Repos, profile: Profile):
    await state.clear()
    await repos.users.delete(profile.id)
    await show(callback, Screen("✅ Все данные удалены. Нажми /start, чтобы начать с чистого листа."))
