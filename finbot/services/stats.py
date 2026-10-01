"""Сводки для экранов статистики. Считают по данным одного пользователя."""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..repos import Repos
from ..repos.users import Profile
from . import dates
from .money import fmt, pct


@dataclass
class Overview:
    key: str
    start: date
    end: date
    label: str
    income: int
    expense: int
    prev_income: int | None
    prev_expense: int | None
    passive: int
    top_expenses: list = field(default_factory=list)
    top_income: list = field(default_factory=list)
    days: int = 1
    forecast: int | None = None

    @property
    def net(self) -> int:
        return self.income - self.expense

    @property
    def savings_rate(self) -> float | None:
        """Доля дохода, которую удалось не потратить. None, если дохода не было."""
        if self.income <= 0:
            return None
        return (self.income - self.expense) / self.income * 100

    @property
    def avg_per_day(self) -> int:
        return self.expense // max(self.days, 1)


def _previous_period(key: str, start: date, end: date, today: date):
    """Предыдущий период такой же длины для сравнения. Для «всё время» его нет."""
    if key == "a":
        return None
    if dates.is_month_period(key):
        prev = dates.add_months(start, -1)
        return dates.month_bounds(prev.year, prev.month)
    length = (end - start).days + 1
    return start - timedelta(days=length), start - timedelta(days=1)


async def overview(repos: Repos, profile: Profile, key: str) -> Overview:
    today = profile.today
    start, end, label = dates.resolve(key, today)
    uid = profile.id
    income, expense = await repos.transactions.totals(uid, start, end)
    prev = _previous_period(key, start, end, today)
    prev_income = prev_expense = None
    if prev:
        prev_income, prev_expense = await repos.transactions.totals(uid, *prev)

    ov = Overview(
        key=key, start=start, end=end, label=label, income=income, expense=expense,
        prev_income=prev_income, prev_expense=prev_expense,
        passive=await repos.transactions.passive_income(uid, start, end),
        top_expenses=list(await repos.transactions.by_category(uid, "expense", start, end)),
        top_income=list(await repos.transactions.by_source(uid, start, end)),
    )

    effective_end = min(end, today)
    if key == "a":
        first = await repos.db.scalar(
            "SELECT MIN(day) FROM transactions WHERE user_id = ?", (uid,), default=None
        )
        start_for_days = date.fromisoformat(first) if first else today
        ov.days = (effective_end - start_for_days).days + 1
    else:
        ov.days = max((effective_end - start).days + 1, 1)

    # Прогноз расходов до конца месяца — только пока месяц идёт
    if start <= today <= end and dates.is_month_period(key) and expense > 0:
        days_total = calendar.monthrange(start.year, start.month)[1]
        elapsed = (today - start).days + 1
        ov.forecast = int(expense / elapsed * days_total)
    return ov


def delta_text(current: int, previous: int | None, symbol: str, good_when_up: bool) -> str:
    """'▲ 12% к прошлому периоду' с цветом-смыслом: рост расходов — плохо, доходов — хорошо."""
    if previous is None:
        return ""
    if previous == 0:
        return "" if current == 0 else "новое"
    change = (current - previous) / previous * 100
    if abs(change) < 0.5:
        return "без изменений"
    arrow = "▲" if change > 0 else "▼"
    good = (change > 0) == good_when_up
    mark = "🟢" if good else "🔴"
    return f"{mark} {arrow} {abs(change):.0f}%"


def insights(ov: Overview, symbol: str, budget_total: int | None = None) -> list[str]:
    """Короткие выводы под сводкой. Не больше трёх — иначе никто не читает."""
    notes: list[str] = []
    rate = ov.savings_rate
    if rate is not None and ov.income > 0:
        if rate >= 20:
            notes.append(f"💚 Откладываешь {rate:.0f}% дохода — отличный темп.")
        elif rate >= 0:
            notes.append(f"🟡 Остаётся {rate:.0f}% дохода. Цель — от 20%.")
        else:
            notes.append(f"🔴 Расходы больше доходов на {fmt(-ov.net, symbol)}.")
    elif ov.expense > 0 and ov.income == 0:
        notes.append("ℹ️ Доходов за период нет — запиши их, чтобы увидеть норму сбережений.")

    if ov.top_expenses and ov.expense > 0:
        top = ov.top_expenses[0]
        share = pct(top["total"], ov.expense)
        if share >= 40:
            notes.append(f"🎯 {top['emoji']} {top['name']} съедает {share:.0f}% расходов.")

    if ov.forecast is not None:
        line = f"🔮 К концу месяца расходы выйдут на ≈{fmt(ov.forecast, symbol)}"
        if budget_total and ov.forecast > budget_total:
            line += " — это выше общего лимита."
        notes.append(line + ("" if line.endswith(".") else "."))

    if ov.income > 0 and ov.passive > 0:
        notes.append(f"🌱 Пассивный доход: {pct(ov.passive, ov.income):.0f}% от всех доходов.")
    return notes[:3]


async def monthly_series(repos: Repos, profile: Profile, months: int = 6):
    """[(метка, доход, расход)] за последние месяцы, включая текущий."""
    today = profile.today
    first = dates.add_months(today.replace(day=1), -(months - 1))
    data = await repos.transactions.monthly(profile.id, first, today)
    series = []
    for offset in range(months):
        month = dates.add_months(first, offset)
        income, expense = data.get(dates.month_key(month), (0, 0))
        series.append((dates.MONTHS_SHORT[month.month - 1], income, expense))
    return series


async def capital_series(repos: Repos, profile: Profile, months: int = 12):
    """Капитал на конец каждого месяца: стартовые остатки + накопленный доход − расход."""
    today = profile.today
    first = dates.add_months(today.replace(day=1), -(months - 1))
    base = await repos.accounts.initial_total(profile.id)
    # всё, что было до окна, входит в стартовую точку
    before_income, before_expense = await repos.transactions.totals(
        profile.id, dates.ALL_TIME_START, first - timedelta(days=1)
    )
    running = base + before_income - before_expense
    data = await repos.transactions.monthly(profile.id, first, today)
    series = []
    for offset in range(months):
        month = dates.add_months(first, offset)
        income, expense = data.get(dates.month_key(month), (0, 0))
        running += income - expense
        series.append((dates.MONTHS_SHORT[month.month - 1], running))
    return series


async def daily_series(repos: Repos, profile: Profile, start: date, end: date):
    """Накопленные расходы по дням месяца: [(день, всего_к_этому_дню)]."""
    today = profile.today
    stop = min(end, today)
    per_day = await repos.transactions.daily(profile.id, "expense", start, stop)
    running, series = 0, []
    cursor = start
    while cursor <= stop:
        running += per_day.get(cursor.isoformat(), 0)
        series.append((cursor.day, running))
        cursor += timedelta(days=1)
    return series
