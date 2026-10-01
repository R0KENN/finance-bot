"""Отчёт за месяц в виде статьи (с таблицами) — для Rich Message."""
from __future__ import annotations

from ..repos import Repos
from ..repos.users import Profile
from . import dates, stats
from .money import fmt, pct


def _cell(value: str) -> str:
    return value.replace("|", "/").replace("\n", " ").strip()


async def month_report(repos: Repos, profile: Profile, key: str) -> tuple[str, str]:
    """(заголовок, тело в разметке статьи) за месяц `key` вида 'YYYY-MM'."""
    sym = profile.symbol
    ov = await stats.overview(repos, profile, key)
    total_budget = next((b["amount"] for b in await repos.budgets.list(profile.id)
                         if b["category_id"] is None), None)
    title = f"Отчёт · {ov.label}"

    lines = [f"Доходы {fmt(ov.income, sym)}, расходы {fmt(ov.expense, sym)}, "
             f"итог {fmt(ov.net, sym, sign=True)}.", "", "# Сводка", "| Показатель | Значение |"]
    lines.append(f"| Доходы | {fmt(ov.income, sym)} |")
    lines.append(f"| Расходы | {fmt(ov.expense, sym)} |")
    lines.append(f"| Итог | {fmt(ov.net, sym, sign=True)} |")
    if ov.savings_rate is not None:
        lines.append(f"| Норма сбережений | {ov.savings_rate:.0f}% |")
    if ov.expense:
        lines.append(f"| Средний расход в день | {fmt(ov.avg_per_day, sym)} |")
    if ov.passive:
        lines.append(f"| Пассивный доход | {fmt(ov.passive, sym)} |")

    if ov.top_expenses:
        lines += ["", "# Расходы по категориям", "| Категория | Сумма | Доля |"]
        for row in ov.top_expenses[:12]:
            lines.append(f"| {_cell(row['emoji'] + ' ' + row['name'])} | {fmt(row['total'], sym)} | "
                         f"{pct(row['total'], ov.expense):.0f}% |")
    if ov.top_income:
        lines += ["", "# Доходы по источникам", "| Источник | Сумма | Доля |"]
        for row in ov.top_income[:12]:
            lines.append(f"| {_cell(row['emoji'] + ' ' + row['name'])} | {fmt(row['total'], sym)} | "
                         f"{pct(row['total'], ov.income):.0f}% |")

    notes = stats.insights(ov, sym, total_budget)
    if notes:
        lines += ["", "# Выводы"] + [f"- {note}" for note in notes]

    goals = await repos.goals.list(profile.id)
    if goals:
        lines += ["", "# Копилки", "| Копилка | Накоплено | Цель |"]
        for g in goals:
            target = fmt(g["target"], sym) if g["target"] else "—"
            lines.append(f"| {_cell(g['emoji'] + ' ' + g['name'])} | {fmt(g['saved'], sym)} | {target} |")

    lines += ["", "---", f"Сформировано ботом {dates.fmt_short(profile.today)} {profile.today.year}."]
    return title, "\n".join(lines)
