"""Стенд: имитация Telegram для сквозных тестов диалогов без сети и токена.

Бот получает обычные сырые обновления, а его вызовы API перехватывает FakeSession.
Тест «нажимает» кнопки по подписям — так проверяется, что нужная кнопка вообще есть.
"""
from __future__ import annotations

import hashlib
import itertools
from html.parser import HTMLParser
from dataclasses import dataclass, field
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import (
    AnswerCallbackQuery, DeleteMessage, EditMessageMedia, EditMessageText, SendDocument,
    SendMessage, SendPhoto, SetMyCommands,
)
from aiogram.types import Chat, Message

from finbot.app import build_dispatcher
from finbot.db import Database
from finbot.repos import Repos
from finbot.services.rich import SendRichMessage
from finbot.ui.common import CB

_ids = itertools.count(1000)

# Рублей за единицу валюты: тесты не ходят в сеть
TEST_RATES = {"RUB": 1.0, "USD": 90.0, "EUR": 100.0, "KZT": 0.2,
              "USDT": 90.0, "USDC": 90.0, "BTC": 6_000_000.0, "ETH": 250_000.0}


async def fake_rates() -> dict[str, float]:
    return dict(TEST_RATES)

# Теги, которые Telegram принимает в parse_mode=HTML
_ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre",
                 "a", "blockquote", "tg-spoiler", "tg-emoji", "span"}


class _HtmlCheck(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _ALLOWED_TAGS:
            self.stack.append(tag)
        else:
            self.errors.append(f"неподдерживаемый тег <{tag}>")

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.errors.append(f"лишний закрывающий </{tag}>")


def html_errors(text: str) -> list[str]:
    """Что в тексте не примет Telegram при parse_mode=HTML."""
    checker = _HtmlCheck()
    checker.feed(text)
    checker.close()
    return checker.errors + [f"не закрыт <{tag}>" for tag in checker.stack]


@dataclass
class Shown:
    """Что сейчас видно в одном сообщении бота."""
    message_id: int
    chat_id: int = 0
    text: str = ""
    markup: object = None
    photo: bool = False
    rich: dict | None = None
    photo_hash: str = ""        # какая картинка показана (md5 содержимого)

    def buttons(self) -> list:
        if not self.markup:
            return []
        return [b for row in self.markup.inline_keyboard for b in row]

    def labels(self) -> list[str]:
        return [b.text for b in self.buttons()]


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.screens: dict[int, Shown] = {}
        self.calls: list = []
        self.alerts: list[str] = []
        self.documents: list = []
        self.fail: dict[str, Exception] = {}      # имя метода -> исключение при вызове
        self.touched: list[int] = []              # порядок обращений к сообщениям
        self.violations: list[str] = []           # нарушения лимитов Telegram

    async def close(self) -> None:
        return None

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        yield b""

    def _message(self, chat_id: int, message_id: int) -> Message:
        return Message(message_id=message_id, date=datetime.now(timezone.utc),
                       chat=Chat(id=chat_id, type="private"))

    def _touch(self, shown: Shown) -> None:
        limit = 1024 if shown.photo else 4096
        if len(shown.text) > limit:
            self.violations.append(f"текст {len(shown.text)} > {limit}: {shown.text[:50]!r}")
        for button in shown.buttons():
            data = button.callback_data or ""
            if len(data.encode()) > 64:
                self.violations.append(f"callback_data {len(data.encode())} байт: {data}")
        for problem in html_errors(shown.text):
            self.violations.append(f"HTML: {problem} в {shown.text[:60]!r}")
        if len(shown.buttons()) > 100:
            self.violations.append("больше 100 кнопок")
        self.screens[shown.message_id] = shown
        self.touched.append(shown.message_id)

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        self.calls.append(method)
        if name in self.fail:
            raise self.fail.pop(name)
        if isinstance(method, SendMessage):
            shown = Shown(next(_ids), method.chat_id, method.text, method.reply_markup)
            self._touch(shown)
            return self._message(method.chat_id, shown.message_id)
        if isinstance(method, SendPhoto):
            shown = Shown(next(_ids), method.chat_id, method.caption or "", method.reply_markup, photo=True,
                          photo_hash=hashlib.md5(method.photo.data).hexdigest())
            self._touch(shown)
            return self._message(method.chat_id, shown.message_id)
        if isinstance(method, SendRichMessage):
            shown = Shown(next(_ids), method.chat_id, "", method.reply_markup, rich=method.rich_message)
            self._touch(shown)
            return self._message(method.chat_id, shown.message_id)
        if isinstance(method, EditMessageText):
            old = self.screens.get(method.message_id)
            if old is None or old.photo or old.rich:
                from aiogram.exceptions import TelegramBadRequest
                raise TelegramBadRequest(method, "message can't be edited")
            shown = Shown(method.message_id, method.chat_id, method.text, method.reply_markup)
            self._touch(shown)
            return self._message(method.chat_id, method.message_id)
        if isinstance(method, EditMessageMedia):
            shown = Shown(method.message_id, method.chat_id, method.media.caption or "", method.reply_markup, photo=True,
                          photo_hash=hashlib.md5(method.media.media.data).hexdigest())
            self._touch(shown)
            return self._message(method.chat_id, method.message_id)
        if isinstance(method, DeleteMessage):
            self.screens.pop(method.message_id, None)
            return True
        if isinstance(method, AnswerCallbackQuery):
            if method.text:
                self.alerts.append(method.text)
            return True
        if isinstance(method, SendDocument):
            self.documents.append(method)
            return self._message(method.chat_id, next(_ids))
        if isinstance(method, SetMyCommands):
            return True
        raise AssertionError(f"Стенд не знает метод {name}")


class Client:
    """Один «пользователь Telegram» со своим чатом."""

    def __init__(self, harness: "Harness", user_id: int, name: str = "Тест"):
        self.h = harness
        self.user_id = user_id
        self.name = name
        self._update = itertools.count(1)

    # ---- что видит пользователь
    @property
    def screen(self) -> Shown:
        for message_id in reversed(self.h.session.touched):
            shown = self.h.session.screens.get(message_id)
            if shown is not None and shown.chat_id == self.user_id:
                return shown
        raise AssertionError("У бота нет ни одного сообщения")

    def sent(self, kind: type) -> list:
        """Вызовы API данного типа в чат этого пользователя."""
        return [c for c in self.h.session.calls
                if isinstance(c, kind) and getattr(c, "chat_id", None) == self.user_id]

    @property
    def text(self) -> str:
        return self.screen.text

    # ---- действия пользователя
    def _user(self) -> dict:
        return {"id": self.user_id, "is_bot": False, "first_name": self.name, "username": f"u{self.user_id}"}

    async def _feed(self, update: dict) -> None:
        update["update_id"] = next(self._update)
        await self.h.dp.feed_raw_update(self.h.bot, update)

    def _msg(self, **extra) -> dict:
        return {
            "message_id": next(_ids), "date": int(datetime.now().timestamp()),
            "chat": {"id": self.user_id, "type": "private"}, "from": self._user(), **extra,
        }

    async def say(self, text: str) -> None:
        await self._feed({"message": self._msg(text=text)})

    async def say_rich(self, blocks: list) -> None:
        await self._feed({"message": self._msg(rich_message={"blocks": blocks})})

    async def press(self, label: str, *, exact: bool = False) -> None:
        shown = self.screen
        matches = [b for b in shown.buttons()
                   if (b.text == label if exact else label in b.text)]
        if not matches:
            raise AssertionError(f"Нет кнопки «{label}». Есть: {shown.labels()}\nЭкран: {shown.text[:200]}")
        await self.press_data(matches[0].callback_data, shown)

    async def press_cb(self, a: str, x="", y="", z="") -> None:
        """Нажать кнопку, которой нет на экране: так проверяем подделку чужих callback_data."""
        await self.press_data(CB(a=a, x=str(x), y=str(y), z=str(z)).pack())

    async def press_data(self, data: str, shown: Shown | None = None) -> None:
        shown = shown or self.screen
        message = {
            "message_id": shown.message_id, "date": int(datetime.now().timestamp()),
            "chat": {"id": self.user_id, "type": "private"},
            "from": {"id": 1, "is_bot": True, "first_name": "bot"},
            "text": shown.text or "x",
        }
        if shown.photo:
            message["photo"] = [{"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1}]
        await self._feed({"callback_query": {
            "id": str(next(_ids)), "from": self._user(), "chat_instance": "ci",
            "message": message, "data": data,
        }})


@dataclass
class Harness:
    db: Database
    repos: Repos
    session: FakeSession
    bot: Bot
    dp: object
    clients: dict = field(default_factory=dict)

    @classmethod
    async def create(cls) -> "Harness":
        db = Database(":memory:")
        await db.connect()
        repos = Repos(db, rate_fetcher=fake_rates)
        await repos.rates.set_table(dict(TEST_RATES))
        session = FakeSession()
        bot = Bot("123456:TEST-TOKEN", session=session,
                  default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
        return cls(db=db, repos=repos, session=session, bot=bot, dp=build_dispatcher(repos))

    def client(self, user_id: int = 1, name: str = "Тест") -> Client:
        if user_id not in self.clients:
            self.clients[user_id] = Client(self, user_id, name)
        return self.clients[user_id]

    async def close(self) -> None:
        await self.db.close()
