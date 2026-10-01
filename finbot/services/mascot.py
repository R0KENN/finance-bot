"""Хомяк — талисман бота: показывает картинку к важным событиям (запись дохода,
распределение денег, достигнутая цель…). Картинки лежат в finbot/assets/hamster."""
from __future__ import annotations

import random
from functools import lru_cache
from pathlib import Path

from ..ui.common import CAPTION_LIMIT, Screen

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "hamster"
SCENES = ("poker", "king", "scales", "ledger", "carpet", "helicopter", "dinner",
          "yacht", "contract", "car", "cigar")

# Какие картинки подходят событию. Если их несколько — выбираем случайную, но не ту же, что в прошлый раз.
SALARY_CATEGORIES = {"Зарплата", "Премия и бонусы"}
EXPENSE_SCENES = {
    "Кафе и рестораны": ["dinner"],
    "Путешествия": ["yacht"],
    "Транспорт": ["car"],
    "Развлечения": ["poker"],
}
POOLS = {
    "income_salary": ["contract"],
    "income_passive": ["king", "ledger"],
    "income": ["cigar", "carpet", "helicopter"],
    "distribute": ["scales"],
    "goal_reached": ["king", "yacht", "helicopter"],
    "debt_closed": ["contract"],
    "digest": ["ledger"],
    "welcome": ["carpet"],
    "overspend": ["poker"],
}

QUIPS = {
    "income_salary": ["Контракт подписан — зарплата в деле 🖋", "Зарплата на месте, хомяк доволен 🐹"],
    "income_passive": ["Деньги работают, пока я отдыхаю 👑", "Пассивный доход — лучший доход 📈"],
    "income": ["Записал! Деньги любят счёт 💰", "Ещё монетка в казну 🪙", "Капитал растёт, щёки тоже 🐹"],
    "dinner": ["Приятного аппетита! Чек я тоже записал 🍽"],
    "yacht": ["Отдых — тоже инвестиция в себя 🌴"],
    "car": ["Дорога стоит денег, но я всё учёл 🚕"],
    "poker": ["Развлечения учтены. Только не ставь последнее 🎲"],
    "distribute": ["Взвесил по плану — всё честно ⚖️"],
    "goal_reached": ["Цель достигнута! Корона тебе к лицу 👑", "Получилось! Пора праздновать 🥂"],
    "debt_closed": ["Долг закрыт, контракт подписан ✍️"],
    "digest": ["Свожу баланс за неделю 📒"],
    "welcome": ["Я — Хомяк, хранитель твоей казны. Добро пожаловать! 🐹"],
    "overspend": ["Лимит превышен — не играй с огнём 🎲"],
}


@lru_cache(maxsize=None)
def load(scene: str) -> bytes:
    return (ASSETS / f"{scene}.jpg").read_bytes()


class Mascot:
    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self._last: dict[int, str] = {}

    def pick(self, user_id: int, pool: list[str]) -> str:
        """Случайная сцена из пула; подряд одну и ту же не показываем, если есть выбор."""
        options = [s for s in pool if s != self._last.get(user_id)] or pool
        scene = self.rng.choice(options)
        self._last[user_id] = scene
        return scene

    def quip(self, key: str) -> str:
        return self.rng.choice(QUIPS.get(key) or [""])


mascot = Mascot()


def income_key(category_name: str | None, passive: bool) -> str:
    if category_name in SALARY_CATEGORIES:
        return "income_salary"
    return "income_passive" if passive else "income"


def with_mascot(screen: Screen, profile, key: str, pool: list[str] | None = None) -> Screen:
    """Добавляет к экрану картинку хомяка и реплику — если пользователь не отключил хомяка
    и всё влезает в подпись к фото (1024 символа). Иначе возвращает экран как был."""
    if not getattr(profile, "mascot", True):
        return screen
    scene = mascot.pick(profile.id, pool or POOLS[key])
    quip = mascot.quip(key if key in QUIPS else scene)
    text = f"{screen.text}\n\n<i>{quip}</i>" if quip else screen.text
    if len(text) > CAPTION_LIMIT:
        text = screen.text
    if len(text) > CAPTION_LIMIT:
        return screen
    return Screen(text, screen.markup, load(scene))


def expense_pool(category_name: str | None) -> list[str] | None:
    return EXPENSE_SCENES.get(category_name or "")
