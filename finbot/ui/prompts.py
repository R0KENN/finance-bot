"""Диалоги «бот спрашивает — пользователь отвечает текстом»."""
from __future__ import annotations

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.types import CallbackQuery, Message

from .common import Screen, cancel_kb, drop, edit_by_id, show


async def ask(
    callback: CallbackQuery, state: FSMContext, next_state: State, text: str,
    back_to: str = "menu", **data,
) -> None:
    """Превращает сообщение с кнопкой в вопрос и ждёт текст.

    id сообщения и текст вопроса запоминаем: когда пользователь ответит,
    правим именно это сообщение, а не засоряем чат новыми.
    """
    await state.set_state(next_state)
    await state.update_data(
        msg=callback.message.message_id, prompt=text, back_to=back_to, **data
    )
    await show(callback, Screen(text, cancel_kb(back_to)))


async def retry(message: Message, state: FSMContext, error: str) -> None:
    """Неверный ввод: удаляем реплику пользователя и показываем вопрос с пояснением."""
    data = await state.get_data()
    await drop(message)
    text = f"⚠️ {error}\n\n{data.get('prompt', '')}"
    await edit_by_id(
        message.bot, message.chat.id, data.get("msg"),
        Screen(text, cancel_kb(data.get("back_to", "menu"))),
    )


async def answer_in_place(message: Message, state: FSMContext, screen: Screen) -> None:
    """Правильный ввод: удаляем реплику и подменяем вопрос следующим экраном."""
    data = await state.get_data()
    await drop(message)
    await edit_by_id(message.bot, message.chat.id, data.get("msg"), screen)
