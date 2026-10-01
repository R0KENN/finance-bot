"""Сквозная логика: доступ, профиль пользователя, ответ на нажатие."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message, TelegramObject

from . import config
from .repos import Repos

log = logging.getLogger(__name__)


class UserMiddleware(BaseMiddleware):
    """Заводит пользователя при первом обращении и кладёт его профиль в хэндлер.

    Любые данные в боте принадлежат конкретному user_id: профиль — единственный
    источник этого id для хэндлеров, а не поля из пришедших данных.
    """

    def __init__(self, repos: Repos):
        self.repos = repos

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None or user.is_bot:
            return None

        if config.ALLOWED_USERS and user.id not in config.ALLOWED_USERS:
            if isinstance(event, Message):
                await event.answer("🔒 Этот бот закрытый.")
            elif isinstance(event, CallbackQuery):
                await event.answer("🔒 Этот бот закрытый.", show_alert=True)
            return None

        existing = await self.repos.users.get(user.id)
        profile = existing or await self.repos.ensure_user(user.id, user.first_name, user.username)
        data["profile"] = profile
        data["repos"] = self.repos
        data["is_new"] = existing is None
        return await handler(event, data)


class CallbackAnswerMiddleware(BaseMiddleware):
    """Гасит «часики» на кнопке после обработки, если хэндлер сам не ответил."""

    async def __call__(self, handler, event: CallbackQuery, data: dict[str, Any]) -> Any:
        result = await handler(event, data)
        try:
            await event.answer()
        except TelegramAPIError:
            pass  # хэндлер уже ответил с подсказкой, или запрос устарел
        return result
