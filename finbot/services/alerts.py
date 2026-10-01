"""Предупреждения о лимитах бюджета сразу после записи расхода."""
from __future__ import annotations

import html
from datetime import date

from ..repos import Repos
from ..repos.users import Profile
from . import dates
from .money import fmt


async def budget_alerts(
    repos: Repos, profile: Profile, category_id: int | None, day: date, amount: int
) -> list[str]:
    """Что сказать пользователю про лимиты после траты `amount` в `day`.

    Предупреждаем в момент пересечения 80% и 100%, а после превышения —
    коротким напоминанием: «уже сверх лимита».
    """
    start, end = dates.month_bounds(day.year, day.month)
    notes: list[str] = []
    for budget, spent in await repos.budgets.statuses(profile.id, start, end):
        if budget["category_id"] not in (None, category_id):
            continue
        limit = budget["amount"]
        before = spent - amount
        name = "Общий лимит месяца" if budget["category_id"] is None else (
            f"Лимит «{html.escape(budget['cat_name'])}»"
        )
        if spent > limit and before >= limit:
            notes.append(f"🚨 {name} уже превышен: {fmt(spent, profile.symbol)} из "
                         f"{fmt(limit, profile.symbol)}")
        elif spent >= limit:
            notes.append(f"🚨 {name} исчерпан: {fmt(spent, profile.symbol)} из "
                         f"{fmt(limit, profile.symbol)}")
        elif before < limit * 0.8 <= spent:
            notes.append(f"⚠️ {name}: использовано {spent / limit * 100:.0f}% "
                         f"({fmt(spent, profile.symbol)} из {fmt(limit, profile.symbol)})")
    return notes
