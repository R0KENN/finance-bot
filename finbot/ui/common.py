"""Общий каркас интерфейса: callback-данные, кнопки, экран, правка сообщений."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

from aiogram import Bot, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InaccessibleMessage, InlineKeyboardButton,
    InlineKeyboardMarkup, InputMediaPhoto,
)

log = logging.getLogger(__name__)

CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096

# Текстовый ответ в диалоге, но не команда: «/menu» не должно стать суммой или заметкой
TEXT = F.text & ~F.text.startswith("/")


class CB(CallbackData, prefix="f"):
    """Одна фабрика на весь бот: действие и до трёх коротких параметров."""

    a: str
    x: str = ""
    y: str = ""
    z: str = ""


def btn(text: str, a: str, x: str | int = "", y: str | int = "", z: str | int = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text, callback_data=CB(a=a, x=str(x), y=str(y), z=str(z)).pack()
    )


def url_btn(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url)


def kb(*rows: Iterable[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(row) for row in rows if row])


def grid(buttons: list[InlineKeyboardButton], cols: int = 2) -> list[list[InlineKeyboardButton]]:
    return [buttons[i:i + cols] for i in range(0, len(buttons), cols)]


def back(to: str = "menu", text: str = "◀️ Назад") -> InlineKeyboardButton:
    return btn(text, "go", to)


def home() -> InlineKeyboardButton:
    return btn("🏠 Меню", "go", "menu")


def cancel_kb(to: str = "menu") -> InlineKeyboardMarkup:
    return kb([btn("✖️ Отмена", "go", to)])


@dataclass
class Screen:
    text: str
    markup: InlineKeyboardMarkup | None = None
    photo: bytes | None = None


def _not_modified(error: TelegramBadRequest) -> bool:
    return "message is not modified" in str(error).lower()


def _fit(screen: Screen) -> Screen:
    """Подпись к фото — максимум 1024 символа. Не влезает — отправляем экран текстом без картинки
    (обрезать нельзя: можно разорвать HTML-тег)."""
    if screen.photo and len(screen.text) > CAPTION_LIMIT:
        return Screen(screen.text, screen.markup, None)
    return screen


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


async def render(bot: Bot, chat_id: int, message_id: int, screen: Screen, is_photo: bool) -> None:
    """Подменяет сообщение экраном. Текст↔фото между собой не редактируются —
    тогда старое сообщение удаляется, а новое отправляется."""
    screen = _fit(screen)
    try:
        if screen.photo and is_photo:
            media = InputMediaPhoto(
                media=BufferedInputFile(screen.photo, "chart.png"), caption=_clip(screen.text, CAPTION_LIMIT)
            )
            await bot.edit_message_media(
                media=media, chat_id=chat_id, message_id=message_id, reply_markup=screen.markup
            )
            return
        if not screen.photo and not is_photo:
            await bot.edit_message_text(
                _clip(screen.text, TEXT_LIMIT), chat_id=chat_id, message_id=message_id, reply_markup=screen.markup
            )
            return
    except TelegramBadRequest as error:
        if _not_modified(error):
            return
        log.info("Не удалось отредактировать сообщение (%s), отправляю заново", error)

    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramBadRequest:
        pass  # слишком старое или уже удалено — не страшно
    await send(bot, chat_id, screen)


async def send(bot: Bot, chat_id: int, screen: Screen):
    screen = _fit(screen)
    if screen.photo:
        return await bot.send_photo(
            chat_id, BufferedInputFile(screen.photo, "chart.png"),
            caption=_clip(screen.text, CAPTION_LIMIT), reply_markup=screen.markup,
        )
    return await bot.send_message(chat_id, _clip(screen.text, TEXT_LIMIT), reply_markup=screen.markup)


async def show(callback: CallbackQuery, screen: Screen) -> None:
    """Показывает экран на месте сообщения, с которого нажали кнопку."""
    message = callback.message
    if message is None or isinstance(message, InaccessibleMessage):
        await send(callback.bot, callback.from_user.id, screen)
        return
    await render(callback.bot, message.chat.id, message.message_id, screen, bool(message.photo))


async def edit_by_id(bot: Bot, chat_id: int, message_id: int | None, screen: Screen) -> None:
    """Правка экрана по сохранённому id (после ввода текста пользователем)."""
    if not message_id:
        await send(bot, chat_id, screen)
        return
    await render(bot, chat_id, message_id, screen, is_photo=False)


async def drop(message) -> None:
    """Убирает сообщение пользователя, чтобы чат оставался чистым."""
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
