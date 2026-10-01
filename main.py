"""Запуск бота: python main.py"""
import asyncio
import logging
import sys

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError

from finbot import config, scheduler
from finbot.app import build_dispatcher, set_commands
from finbot.db import Database
from finbot.repos import Repos


async def main() -> None:
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not config.BOT_TOKEN:
        sys.exit("Не задан BOT_TOKEN. Скопируй .env.example в .env и впиши токен от @BotFather.")

    db = Database(config.DB_PATH)
    await db.connect()
    repos = Repos(db)

    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(
        parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    background = None
    try:
        try:
            await set_commands(bot)
        except TelegramUnauthorizedError:
            sys.exit("Telegram не принял токен. Проверь BOT_TOKEN в .env (его выдаёт @BotFather).")
        except TelegramNetworkError as error:
            sys.exit(f"Нет связи с Telegram: {error}")

        dp = build_dispatcher(repos)
        background = asyncio.create_task(scheduler.run(bot, repos), name="scheduler")
        logging.getLogger("finbot").info("Бот запущен")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        if background:
            background.cancel()
        await bot.session.close()
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
