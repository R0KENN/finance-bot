"""Сборка диспетчера: middleware, роутеры, обработчик ошибок."""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent

from .handlers import build_router
from .middlewares import CallbackAnswerMiddleware, ThrottleMiddleware, UserMiddleware
from .repos import Repos

log = logging.getLogger(__name__)


def build_dispatcher(repos: Repos, *, rate_limit: bool = True) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.workflow_data["repos"] = repos

    # Бот личный: в группах молчим
    dp.message.filter(F.chat.type == "private")

    if rate_limit:
        throttle = ThrottleMiddleware()  # один на оба типа событий: лимит общий на пользователя
        dp.message.outer_middleware(throttle)
        dp.callback_query.outer_middleware(throttle)
    dp.message.outer_middleware(UserMiddleware(repos))
    dp.callback_query.outer_middleware(UserMiddleware(repos))
    dp.callback_query.middleware(CallbackAnswerMiddleware())

    dp.include_router(build_router())

    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        log.exception("Ошибка при обработке обновления", exc_info=event.exception)
        update = event.update
        try:
            if update.callback_query:
                await update.callback_query.answer(
                    "Что-то пошло не так. Попробуй ещё раз или открой меню.", show_alert=True
                )
            elif update.message:
                await update.message.answer("⚠️ Что-то пошло не так. Открой меню: /menu")
        except Exception:  # noqa: BLE001 — ответ об ошибке сам не должен падать
            log.exception("Не удалось сообщить пользователю об ошибке")
        return True

    return dp


async def set_commands(bot: Bot) -> None:
    from aiogram.types import BotCommand
    await bot.set_my_commands([
        BotCommand(command="menu", description="Главное меню"),
        BotCommand(command="add", description="Добавить доход или расход"),
        BotCommand(command="stats", description="Статистика и графики"),
        BotCommand(command="help", description="Как пользоваться"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
    ])
