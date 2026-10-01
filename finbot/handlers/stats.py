"""Статистика: сводка за период и графики."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from ..repos import Repos
from ..repos.users import Profile
from ..services import charts, dates, stats
from ..services.money import bar, fmt
from ..ui.common import CB, Screen, btn, kb, show
from ..ui.format import esc, money
from ..ui.nav import section

router = Router(name="stats")

PERIODS = [("d", "День"), ("w", "Неделя"), ("m", "Месяц"), ("y", "Год"), ("a", "Всё")]
CHARTS = [("cat", "🍩 Расходы"), ("src", "💰 Источники"), ("trend", "📈 Месяцы"),
          ("daily", "📉 По дням"), ("cap", "🏦 Капитал")]


def _norm_period(key: str) -> str:
    return key if key in {"d", "w", "m", "y", "a"} or dates.is_month_period(key) else "m"


async def _chart(repos: Repos, profile: Profile, ov: stats.Overview, chart: str) -> bytes | None:
    sym = profile.symbol
    if chart == "cat":
        rows = [(f"{r['emoji']} {r['name']}", r["total"]) for r in ov.top_expenses]
        return await charts.expense_donut("Расходы по категориям", ov.label, rows, fmt(ov.expense, sym))
    if chart == "src":
        rows = [(f"{r['emoji']} {r['name']}", r["total"]) for r in ov.top_income]
        return await charts.income_bars("Доходы по источникам", ov.label, rows)
    if chart == "trend":
        series = await stats.monthly_series(repos, profile, 6)
        return await charts.months_bars("Доходы и расходы", "последние 6 месяцев", series)
    if chart == "daily":
        start, end, label = (ov.start, ov.end, ov.label) if dates.is_month_period(ov.key) else (
            *dates.month_bounds(profile.today.year, profile.today.month)[:2],
            f"{dates.MONTHS_NOM[profile.today.month - 1]} {profile.today.year}")
        series = await stats.daily_series(repos, profile, start, end)
        limit = next((b["amount"] for b in await repos.budgets.list(profile.id)
                      if b["category_id"] is None), None)
        return await charts.cumulative_line("Расходы нарастающим итогом", label, series, limit)
    if chart == "cap":
        series = await stats.capital_series(repos, profile, 12)
        return await charts.capital_line("Капитал по месяцам", "последние 12 месяцев", series)
    return None


def _summary(profile: Profile, ov: stats.Overview, notes: list[str]) -> str:
    sym = profile.symbol
    inc_delta = stats.delta_text(ov.income, ov.prev_income, sym, good_when_up=True)
    exp_delta = stats.delta_text(ov.expense, ov.prev_expense, sym, good_when_up=False)
    lines = [
        f"📊 <b>{esc(ov.label)}</b>",
        "",
        f"▲ Доходы  <b>{money(profile, ov.income)}</b>  {inc_delta}".rstrip(),
        f"▼ Расходы  <b>{money(profile, ov.expense)}</b>  {exp_delta}".rstrip(),
        f"━ Итог  <b>{money(profile, ov.net, sign=True)}</b>",
    ]
    rate = ov.savings_rate
    if rate is not None:
        lines.append(f"💾 Сбережения {bar(max(rate, 0) / 100, 10)} {rate:.0f}%")
    if ov.expense:
        lines.append(f"📆 В среднем в день: {money(profile, ov.avg_per_day)}")
    if notes:
        lines += [""] + [esc(note) for note in notes]
    return "\n".join(lines)


async def stats_screen(repos: Repos, profile: Profile, period: str = "m",
                       chart: str = "cat") -> Screen:
    period = _norm_period(period)
    chart = chart if chart in dict(CHARTS) else "cat"
    ov = await stats.overview(repos, profile, period)
    total_budget = next((b["amount"] for b in await repos.budgets.list(profile.id)
                         if b["category_id"] is None), None)
    notes = stats.insights(ov, profile.symbol, total_budget)
    text = _summary(profile, ov, notes)
    photo = await _chart(repos, profile, ov, chart)
    if photo is None and chart in ("cat", "src", "daily", "cap"):
        text += "\n\n<i>Для этого графика пока мало данных.</i>"

    today = profile.today
    periods = [btn(("• " if key == period else "") + name, "st", key, chart) for key, name in PERIODS]
    rows = [periods]
    if dates.is_month_period(period):
        prev_key = dates.shift_month_key(period, -1, today)
        next_key = dates.shift_month_key(period, 1, today)
        nav = [btn("◀️ " + dates.MONTHS_SHORT[int(prev_key[5:]) - 1], "st", prev_key, chart),
               btn("📅 Месяц", "st", "m", chart)]
        if next_key <= dates.month_key(today):
            nav.append(btn(dates.MONTHS_SHORT[int(next_key[5:]) - 1] + " ▶️", "st", next_key, chart))
        rows.append(nav)
    rows.append([btn(("• " if key == chart else "") + name, "st", period, key)
                 for key, name in CHARTS[:3]])
    rows.append([btn(("• " if key == chart else "") + name, "st", period, key)
                 for key, name in CHARTS[3:]])
    actions = [btn("🧾 Операции", "hist", period, "a", 0)]
    if dates.is_month_period(period):
        actions.append(btn("📰 Отчёт-статья", "rep", dates.month_key(ov.start)))
    rows.append(actions)
    rows.append([btn("🏠 Меню", "go", "menu")])
    return Screen(text, kb(*rows), photo)


@section("stats")
async def stats_section(repos: Repos, profile: Profile) -> Screen:
    return await stats_screen(repos, profile)


@router.callback_query(CB.filter(F.a == "st"))
async def on_stats(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    await show(callback, await stats_screen(repos, profile, callback_data.x or "m",
                                            callback_data.y or "cat"))
