"""Фоновые задачи раз в минуту: регулярные операции, напоминания, недельный дайджест."""
from __future__ import annotations

import asyncio
import html
import logging
from datetime import date, datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from .repos import Repos
from .repos.users import Profile
from .services import backup, dates
from .services.mascot import with_mascot
from .services.money import decimals_of, fmt, symbol_of
from .ui.common import Screen, btn, kb, send

log = logging.getLogger(__name__)

DIGEST_HOUR = 9
DEBT_HOUR = 10
MAX_CATCH_UP = 62          # не создаём больше двух месяцев «пропущенных» операций за раз


async def apply_recurring(repos: Repos, profile: Profile) -> list[str]:
    """Создаёт операции по расписанию, включая пропущенные дни. Возвращает строки для уведомления."""
    created: list[str] = []
    today = profile.today
    for item in await repos.recurring.due(profile.id, today):
        next_day = date.fromisoformat(item["next_day"])
        steps = 0
        while next_day <= today and steps < MAX_CATCH_UP:
            await repos.transactions.add(
                profile.id, item["kind"], item["amount"], item["account_id"], next_day,
                category_id=item["category_id"], source_id=item["source_id"],
                note=item["note"], recurring_id=item["id"],
            )
            next_day = dates.next_occurrence(item["freq"], item["param"], next_day)
            steps += 1
            created.append(
                f"{'➕' if item['kind'] == 'income' else '➖'} "
                f"{html.escape(item['note'] or item['cat_name'] or 'Операция')} · "
                f"{fmt(item['amount'], symbol_of(item['acc_currency']), decimals=decimals_of(item['acc_currency']))}"
            )
        if steps >= MAX_CATCH_UP and next_day <= today:
            next_day = dates.next_occurrence(item["freq"], item["param"], today)
        await repos.recurring.set_next(profile.id, item["id"], next_day)
    return created


async def process_user(bot: Bot, repos: Repos, profile: Profile, now: datetime | None = None) -> None:
    local = (now or datetime.now(profile.tz)).astimezone(profile.tz)
    today = local.date()

    created = await apply_recurring(repos, profile)
    if created:
        shown = "\n".join(created[:15]) + (f"\n…и ещё {len(created) - 15}" if len(created) > 15 else "")
        await send(bot, profile.id, Screen(
            f"🔁 <b>Добавлено по расписанию</b>\n\n{shown}",
            kb([btn("🧾 История", "go", "hist"), btn("🏠 Меню", "go", "menu")])))

    if profile.reminder_hour is not None and local.hour == profile.reminder_hour \
            and profile.last_reminder_day != today.isoformat():
        await repos.users.update(profile.id, last_reminder_day=today.isoformat())
        profile.last_reminder_day = today.isoformat()
        if not await repos.transactions.has_any_on(profile.id, today):
            await send(bot, profile.id, Screen(
                "🌙 Сегодня ещё нет записей. Что потратил или заработал? "
                "Напиши, например: <code>кофе 250</code>",
                kb([btn("➖ Расход", "add", "expense"), btn("➕ Доход", "add", "income")])))

    if local.hour == DEBT_HOUR and profile.last_debt_day != today.isoformat():
        await repos.users.update(profile.id, last_debt_day=today.isoformat())
        profile.last_debt_day = today.isoformat()
        due = await repos.debts.due_on(profile.id, today)
        if due:
            lines = [f"{'📥' if d['direction'] == 'owed' else '📤'} <b>{html.escape(d['person'])}</b> · "
                     f"{fmt(d['amount'] - d['paid'], profile.symbol)}" for d in due]
            await send(bot, profile.id, Screen(
                "⏰ <b>Сегодня срок по долгам</b>\n\n" + "\n".join(lines),
                kb([btn("🤝 Долги", "go", "debts")])))

    week_id = f"{local.isocalendar().year}-W{local.isocalendar().week:02d}"
    if profile.digest and local.weekday() == 0 and local.hour == DIGEST_HOUR \
            and profile.last_digest_week != week_id:
        await repos.users.update(profile.id, last_digest_week=week_id)
        profile.last_digest_week = week_id
        await send_digest(bot, repos, profile, today)


async def send_digest(bot: Bot, repos: Repos, profile: Profile, today) -> bool:
    """Итоги прошедшей недели. Если записей не было — молчим."""
    end = today - timedelta(days=1)
    start = end - timedelta(days=6)
    income, expense = await repos.transactions.totals(profile.id, start, end)
    if not income and not expense:
        return False
    top = await repos.transactions.by_category(profile.id, "expense", start, end)
    sym = profile.symbol
    lines = [f"📬 <b>Итоги недели</b> · {dates.fmt_short(start)} – {dates.fmt_short(end)}", "",
             f"▲ Доходы  <b>{fmt(income, sym)}</b>",
             f"▼ Расходы  <b>{fmt(expense, sym)}</b>",
             f"━ Итог  <b>{fmt(income - expense, sym, sign=True)}</b>"]
    if top:
        lines += ["", f"🏆 Больше всего ушло на {top[0]['emoji']} {top[0]['name']} — "
                      f"{fmt(top[0]['total'], sym)}"]
    screen = Screen("\n".join(lines),
                    kb([btn("📊 Подробнее", "go", "stats"), btn("🏠 Меню", "go", "menu")]))
    await send(bot, profile.id, with_mascot(screen, profile, "digest"))
    return True


async def tick(bot: Bot, repos: Repos) -> None:
    await repos.rates.refresh_if_stale()          # раз в несколько часов; при сбое сеть не дёргаем часто
    for profile in await repos.users.for_scheduler():
        try:
            await process_user(bot, repos, profile)
        except TelegramForbiddenError:
            # Пользователь заблокировал бота: больше не пишем ему сами
            await repos.users.update(profile.id, reminder_hour=None, digest=0)
        except Exception:  # noqa: BLE001 — сбой у одного пользователя не должен остановить остальных
            log.exception("Фоновая задача для %s упала", profile.id)


async def run(bot: Bot, repos: Repos, interval: float = 60) -> None:
    log.info("Планировщик запущен")
    while True:
        try:
            await tick(bot, repos)
        except Exception:  # noqa: BLE001
            log.exception("Сбой тика планировщика")
        try:
            await backup.tick(bot)
        except Exception:  # noqa: BLE001
            log.exception("Сбой отправки копии базы")
        await asyncio.sleep(interval)
