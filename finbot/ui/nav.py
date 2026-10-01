"""Реестр разделов: по имени получаем экран. Нужен кнопкам «Назад» и «Отмена»."""
from __future__ import annotations

from typing import Awaitable, Callable

from ..repos import Repos
from ..repos.users import Profile
from .common import Screen

Builder = Callable[[Repos, Profile], Awaitable[Screen]]
SECTIONS: dict[str, Builder] = {}


def section(name: str):
    def register(func: Builder) -> Builder:
        SECTIONS[name] = func
        return func
    return register


async def build_section(name: str, repos: Repos, profile: Profile) -> Screen:
    builder = SECTIONS.get(name) or SECTIONS["menu"]
    return await builder(repos, profile)
