"""Отправка «Статей» Telegram (Rich Messages, Bot API 10.1).

aiogram пока не знает метода sendRichMessage, поэтому описываем его сами.
Если Telegram метод не принял (старый сервер, лимит, изменившаяся схема),
статья уходит обычным HTML-сообщением — пользователь всё равно её получит.
"""
from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramNotFound
from aiogram.methods.base import TelegramMethod
from aiogram.types import InlineKeyboardMarkup, Message

from .. import config
from . import article_markup as markup

log = logging.getLogger(__name__)


class SendRichMessage(TelegramMethod[Message]):
    """https://core.telegram.org/bots/api#sendrichmessage"""

    __returning__ = Message
    __api_method__ = "sendRichMessage"

    chat_id: int
    rich_message: dict[str, Any]
    reply_markup: InlineKeyboardMarkup | None = None


class RichState:
    """Помнит, что метод недоступен, чтобы не стучаться в него на каждую статью."""

    enabled = config.RICH_MESSAGES


async def send_article(
    bot: Bot, chat_id: int, title: str, body: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
    """Отправляет статью как Rich Message, а при отказе — как HTML. Возвращает последнее сообщение."""
    if RichState.enabled:
        try:
            return await bot(SendRichMessage(
                chat_id=chat_id,
                rich_message=markup.to_rich_message(title, body),
                reply_markup=reply_markup,
            ))
        except TelegramNotFound:
            # 404 — метода нет на этом сервере: дальше не пробуем до перезапуска
            RichState.enabled = False
            log.warning("sendRichMessage недоступен, перехожу на обычные сообщения")
        except TelegramBadRequest as error:
            log.warning("Статья не принята как Rich Message (%s), шлю HTML", error)

    parts = markup.to_html_parts(title, body)
    sent: Message | None = None
    for index, part in enumerate(parts):
        is_last = index == len(parts) - 1
        sent = await bot.send_message(
            chat_id, part, parse_mode="HTML", disable_web_page_preview=True,
            reply_markup=reply_markup if is_last else None,
        )
    return sent
