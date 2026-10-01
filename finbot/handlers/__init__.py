"""Сборка всех роутеров. Порядок важен: сначала узкие (по состояниям), потом общий быстрый ввод."""
import importlib

from aiogram import Router

from . import (
    accounts, add, articles, budgets, categories, currencies, debts, goals, history, quick, recurring,
    settings, start, stats,
)

# add раньше quick: быстрый ввод пользуется экранами добавления
_MODULES = [
    start, add, history, stats, budgets, goals, accounts, categories,
    recurring, debts, currencies, settings, articles, quick,
]
_used = False


def build_router() -> Router:
    """Новый корневой роутер.

    Роутер aiogram можно подключить только к одному родителю, а роутеры лежат в
    модулях. Поэтому при повторной сборке (тесты, перезапуск диспетчера в одном
    процессе) модули перезагружаются — и получают свежие роутеры.
    """
    global _used, _MODULES
    if _used:
        _MODULES = [importlib.reload(module) for module in _MODULES]
    _used = True
    root = Router(name="root")
    for module in _MODULES:
        root.include_router(module.router)
    return root
