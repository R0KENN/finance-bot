"""Копилки и план распределения доходов по процентам."""
from __future__ import annotations

from datetime import date, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..defaults import PLAN_TEMPLATES
from ..repos import Repos
from ..repos.goals import split_by_plan
from ..repos.users import Profile
from ..services import charts, dates
from ..services.mascot import with_mascot
from ..services.money import bar, parse_amount
from ..states import GoalS
from ..ui.common import CB, TEXT, Screen, btn, grid, kb, show
from ..ui.format import esc, label, money, split_emoji, valid_name
from ..ui.nav import section
from ..ui.prompts import answer_in_place, ask, retry

router = Router(name="goals")


def _percent_text(value: str) -> float | None:
    """'10', '7,5', '10%' -> число; '-' или '0' -> 0. Иначе None."""
    raw = value.strip().replace(",", ".").rstrip("%").strip()
    if raw in ("-", "—", ""):
        return 0.0
    try:
        number = float(raw)
    except ValueError:
        return None
    return number if 0 <= number <= 100 else None


def _fmt_percent(value: float) -> str:
    return f"{value:g}%"


# ---------------------------------------------------------------- список

async def _runway_months(repos: Repos, profile: Profile, capital: int) -> float | None:
    """На сколько месяцев хватит капитала при среднем расходе за последние 90 дней."""
    today = profile.today
    _, spent = await repos.transactions.totals(profile.id, today - timedelta(days=89), today)
    if spent <= 0 or capital <= 0:
        return None
    return capital / (spent / 3)


async def goals_screen(repos: Repos, profile: Profile) -> Screen:
    uid = profile.id
    goals = await repos.goals.list(uid)
    capital = await repos.accounts.total(uid)
    saved = await repos.goals.total_saved(uid)
    plan = sum(g["percent"] for g in goals)

    lines = ["🎯 <b>Копилки и план</b>", "",
             f"🏦 Капитал <b>{money(profile, capital)}</b>",
             f"├ 🎯 В копилках  {money(profile, saved)}",
             f"└ 💵 Свободно  {money(profile, capital - saved)}", ""]
    runway = await _runway_months(repos, profile, capital)
    if runway is not None:
        lines += [f"🛡 Капитала хватит примерно на <b>{runway:.1f} мес.</b> текущих расходов "
                  "(финансисты советуют держать 3–6).", ""]
    if not goals:
        lines += [
            "Копилка — это цель или «конверт» для денег: подушка безопасности, отпуск, "
            "инвестиции. Задай процент — и каждый новый доход можно разложить по плану "
            "одной кнопкой.",
        ]
    for g in goals:
        head = f"{label(g['emoji'], g['name'])}"
        head += f" · {_fmt_percent(g['percent'])} дохода" if g["percent"] else ""
        if g["target"]:
            ratio = g["saved"] / g["target"]
            lines += [f"<b>{esc(head)}</b>", f"{bar(ratio, 10)} {min(ratio * 100, 999):.0f}% · "
                      f"{money(profile, g['saved'])} из {money(profile, g['target'])}"]
        else:
            lines.append(f"<b>{esc(head)}</b> · {money(profile, g['saved'])}")
    if goals:
        lines += ["", f"📐 План: <b>{_fmt_percent(plan)}</b> дохода уходит в копилки, "
                      f"{_fmt_percent(max(0.0, 100 - plan))} остаётся свободными."]
        if plan > 100:
            lines.append("⚠️ В сумме больше 100% — часть процентов не сработает.")

    buttons = [btn(label(g["emoji"], g["name"]), "gl", g["id"]) for g in goals]
    markup = kb(
        *grid(buttons, 2),
        [btn("➕ Новая копилка", "gln"), btn("📐 План", "glp")],
        [btn("💰 Распределить сумму", "gld"), btn("📊 График", "glc")],
        [btn("🏠 Меню", "go", "menu")],
    )
    return Screen("\n".join(lines), markup)


@section("goals")
async def goals_section(repos: Repos, profile: Profile) -> Screen:
    return await goals_screen(repos, profile)


@router.callback_query(CB.filter(F.a == "glc"))
async def on_chart(callback: CallbackQuery, repos: Repos, profile: Profile):
    goals = await repos.goals.list(profile.id)
    photo = await charts.goals_bars("Копилки", "прогресс к целям",
                                    [(f"{g['emoji']} {g['name']}", g["saved"], g["target"])
                                     for g in goals])
    if photo is None:
        await callback.answer("Задай цель хотя бы одной копилке — тогда будет график.",
                              show_alert=True)
        return
    await show(callback, Screen("📊 <b>Прогресс копилок</b>",
                                kb([btn("◀️ Назад", "go", "goals")]), photo))


# ---------------------------------------------------------------- одна копилка

async def goal_screen(repos: Repos, profile: Profile, goal_id: int) -> Screen | None:
    goal = await repos.goals.get(profile.id, goal_id)
    if not goal:
        return None
    lines = [esc(label(goal['emoji'], goal['name'])), "",
             f"💰 Накоплено: <b>{money(profile, goal['saved'])}</b>"]
    if goal["target"]:
        ratio = goal["saved"] / goal["target"]
        lines += [f"🎯 Цель: {money(profile, goal['target'])}",
                  f"{bar(ratio, 12)} {min(ratio * 100, 999):.0f}%"]
        left = goal["target"] - goal["saved"]
        lines.append("✅ Цель достигнута!" if left <= 0 else f"Осталось: {money(profile, left)}")
    lines.append(f"📐 Доля плана: {_fmt_percent(goal['percent'])} с каждого дохода")
    history = await repos.goals.history(profile.id, goal_id)
    if history:
        lines += ["", "<b>Последние движения</b>"]
        today = profile.today
        for op in history:
            day = dates.fmt_day(date.fromisoformat(op["day"]), today)
            note = f" · {esc(op['note'])}" if op["note"] else ""
            lines.append(f"{day}: {money(profile, op['amount'], sign=True)}{note}")
    gid = goal_id
    markup = kb(
        [btn("➕ Пополнить", "glop", "dep", gid), btn("➖ Снять", "glop", "wd", gid)],
        [btn("✏️ Название", "gle", "name", gid), btn("🎯 Цель", "gle", "target", gid)],
        [btn("📐 Процент плана", "gle", "percent", gid)],
        [btn("🗑 Удалить", "gldel", gid), btn("◀️ Назад", "go", "goals")],
    )
    return Screen("\n".join(lines), markup)


@router.callback_query(CB.filter(F.a == "gl"))
async def on_goal(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    screen = await goal_screen(repos, profile, int(callback_data.x or 0))
    if screen is None:
        await show(callback, await goals_screen(repos, profile))
        return
    await show(callback, screen)


@router.callback_query(CB.filter(F.a == "glop"))
async def on_money_op(callback: CallbackQuery, callback_data: CB, state: FSMContext,
                      repos: Repos, profile: Profile):
    goal_id = int(callback_data.y or 0)
    goal = await repos.goals.get(profile.id, goal_id)
    if not goal:
        await callback.answer("Копилка не найдена", show_alert=True)
        return
    deposit = callback_data.x == "dep"
    text = (f"➕ <b>Пополнить «{esc(goal['name'])}»</b>\n\nСколько кладём?" if deposit else
            f"➖ <b>Снять из «{esc(goal['name'])}»</b>\n\nНакоплено {money(profile, goal['saved'])}. "
            "Сколько снимаем?")
    await ask(callback, state, GoalS.deposit if deposit else GoalS.withdraw, text,
              back_to="goals", goal_id=goal_id)


async def _apply_op(message: Message, state: FSMContext, repos: Repos, profile: Profile, sign: int):
    amount = parse_amount(message.text)
    if amount is None:
        await retry(message, state, "Не понял сумму. Пример: 5000 или 5к.")
        return
    data = await state.get_data()
    goal_id = data["goal_id"]
    note = "Пополнение" if sign > 0 else "Снятие"
    before = await repos.goals.get(profile.id, goal_id)
    if not await repos.goals.add_op(profile.id, goal_id, sign * amount, profile.today, note):
        await retry(message, state, "В копилке столько нет.")
        return
    after = await repos.goals.get(profile.id, goal_id)
    screen = await goal_screen(repos, profile, goal_id)
    if sign > 0 and before["target"] and before["saved"] < before["target"] <= after["saved"]:
        screen.text = f"🎉 <b>Цель достигнута!</b>\n\n{screen.text}"
        screen = with_mascot(screen, profile, "goal_reached")
    await answer_in_place(message, state, screen)
    await state.clear()


@router.message(GoalS.deposit, TEXT)
async def on_deposit(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    await _apply_op(message, state, repos, profile, +1)


@router.message(GoalS.withdraw, TEXT)
async def on_withdraw(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    await _apply_op(message, state, repos, profile, -1)


# ---------------------------------------------------------------- создание

@router.callback_query(CB.filter(F.a == "gln"))
async def on_new(callback: CallbackQuery, state: FSMContext):
    await ask(callback, state, GoalS.name,
              "🎯 <b>Новая копилка</b>\n\nКак назвать? Можно с эмодзи: <code>🏖 Отпуск</code>",
              back_to="goals")


@router.message(GoalS.name, TEXT)
async def on_new_name(message: Message, state: FSMContext):
    emoji, name = split_emoji(message.text, "🎯")
    if not valid_name(name):
        await retry(message, state, "Название от 1 до 40 символов.")
        return
    await state.update_data(g_emoji=emoji, g_name=name)
    await state.set_state(GoalS.target)
    await state.update_data(prompt=f"🎯 <b>{esc(name)}</b>\n\nСколько хочешь накопить? "
                                   "Напиши сумму или «-», если цели нет.")
    await answer_in_place(message, state, Screen(
        (await state.get_data())["prompt"], kb([btn("✖️ Отмена", "go", "goals")])))


@router.message(GoalS.target, TEXT)
async def on_new_target(message: Message, state: FSMContext):
    raw = message.text.strip()
    target = None
    if raw not in ("-", "—"):
        target = parse_amount(raw)
        if target is None:
            await retry(message, state, "Не понял сумму. Пример: 300000 или 300к. Или «-».")
            return
    await state.update_data(g_target=target)
    await state.set_state(GoalS.percent)
    prompt = ("📐 <b>Какой процент каждого дохода откладывать сюда?</b>\n\n"
              "Например <code>10</code>. Или «-», если пополнять будешь вручную.")
    await state.update_data(prompt=prompt)
    await answer_in_place(message, state, Screen(prompt, kb([btn("✖️ Отмена", "go", "goals")])))


@router.message(GoalS.percent, TEXT)
async def on_new_percent(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    percent = _percent_text(message.text)
    if percent is None:
        await retry(message, state, "Нужно число от 0 до 100. Или «-».")
        return
    data = await state.get_data()
    goal_id = await repos.goals.add(profile.id, data["g_name"], data["g_emoji"],
                                    data.get("g_target"), percent)
    await answer_in_place(message, state, await goal_screen(repos, profile, goal_id))
    await state.clear()


# ---------------------------------------------------------------- правка и удаление

@router.callback_query(CB.filter(F.a == "gle"))
async def on_edit(callback: CallbackQuery, callback_data: CB, state: FSMContext,
                  repos: Repos, profile: Profile):
    field, goal_id = callback_data.x, int(callback_data.y or 0)
    if not await repos.goals.get(profile.id, goal_id):
        await callback.answer("Копилка не найдена", show_alert=True)
        return
    table = {
        "name": (GoalS.rename, "✏️ Новое название (можно с эмодзи):"),
        "target": (GoalS.set_target, "🎯 Новая цель — сумма. «-» уберёт цель."),
        "percent": (GoalS.set_percent, "📐 Какой процент каждого дохода сюда? От 0 до 100."),
    }
    if field not in table:
        return
    st, text = table[field]
    await ask(callback, state, st, text, back_to="goals", goal_id=goal_id)


@router.message(GoalS.rename, TEXT)
async def on_rename(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    emoji, name = split_emoji(message.text, "🎯")
    if not valid_name(name):
        await retry(message, state, "Название от 1 до 40 символов.")
        return
    goal_id = (await state.get_data())["goal_id"]
    await repos.goals.update(profile.id, goal_id, name=name, emoji=emoji)
    await answer_in_place(message, state, await goal_screen(repos, profile, goal_id))
    await state.clear()


@router.message(GoalS.set_target, TEXT)
async def on_set_target(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    raw = message.text.strip()
    target = None
    if raw not in ("-", "—"):
        target = parse_amount(raw)
        if target is None:
            await retry(message, state, "Не понял сумму. Пример: 300000 или «-».")
            return
    goal_id = (await state.get_data())["goal_id"]
    await repos.goals.update(profile.id, goal_id, target=target)
    await answer_in_place(message, state, await goal_screen(repos, profile, goal_id))
    await state.clear()


@router.message(GoalS.set_percent, TEXT)
async def on_set_percent(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    percent = _percent_text(message.text)
    if percent is None:
        await retry(message, state, "Нужно число от 0 до 100.")
        return
    goal_id = (await state.get_data())["goal_id"]
    await repos.goals.update(profile.id, goal_id, percent=percent)
    await answer_in_place(message, state, await goal_screen(repos, profile, goal_id))
    await state.clear()


@router.callback_query(CB.filter(F.a == "gldel"))
async def on_delete_ask(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    goal = await repos.goals.get(profile.id, int(callback_data.x or 0))
    if not goal:
        await show(callback, await goals_screen(repos, profile))
        return
    note = (f"\n\nНакопленные {money(profile, goal['saved'])} вернутся в свободные деньги."
            if goal["saved"] else "")
    await show(callback, Screen(
        f"🗑 <b>Удалить «{esc(goal['name'])}»?</b>{note}",
        kb([btn("🗑 Да, удалить", "gldely", goal["id"]), btn("◀️ Нет", "gl", goal["id"])]),
    ))


@router.callback_query(CB.filter(F.a == "gldely"))
async def on_delete(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    await repos.goals.delete(profile.id, int(callback_data.x or 0))
    await show(callback, await goals_screen(repos, profile))


# ---------------------------------------------------------------- план и шаблоны

@router.callback_query(CB.filter(F.a == "glp"))
async def on_plan(callback: CallbackQuery, repos: Repos, profile: Profile):
    goals = await repos.goals.list(profile.id)
    plan = sum(g["percent"] for g in goals)
    lines = ["📐 <b>План распределения доходов</b>", "",
             "С каждого дохода выбранные проценты откладываются в копилки, остальное — "
             "на жизнь. Применяется кнопкой «Распределить» после записи дохода.", ""]
    for g in goals:
        lines.append(f"{esc(label(g['emoji'], g['name']))} — <b>{_fmt_percent(g['percent'])}</b>")
    if goals:
        lines.append(f"\n💵 Остаётся свободными: <b>{_fmt_percent(max(0.0, 100 - plan))}</b>")
    lines += ["", "<b>Готовые шаблоны</b> (добавят копилки и проценты):"]
    templates = [btn(title, "glt", key) for key, (title, _, _) in PLAN_TEMPLATES.items()]
    for _, (title, description, _) in PLAN_TEMPLATES.items():
        lines.append(f"{title} — {description}")
    await show(callback, Screen("\n".join(lines), kb(
        *grid(templates, 1), [btn("◀️ Назад", "go", "goals")])))


@router.callback_query(CB.filter(F.a == "glt"))
async def on_template(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    template = PLAN_TEMPLATES.get(callback_data.x)
    if not template:
        return
    existing = {g["name"]: g for g in await repos.goals.list(profile.id)}
    for emoji, name, percent in template[2]:
        if name in existing:
            await repos.goals.update(profile.id, existing[name]["id"], percent=percent)
        else:
            await repos.goals.add(profile.id, name, emoji, None, percent)
    await show(callback, await goals_screen(repos, profile))


# ---------------------------------------------------------------- распределение суммы

async def preview_screen(repos: Repos, profile: Profile, amount: int,
                         confirm: CB) -> Screen:
    goals = await repos.goals.list(profile.id)
    shares = split_by_plan(amount, goals)
    names = {g["id"]: g for g in goals}
    lines = [f"💰 <b>Распределение {money(profile, amount)}</b>", ""]
    if not shares:
        lines.append("В плане нет копилок с процентом. Задай проценты в разделе «План».")
        return Screen("\n".join(lines), kb([btn("📐 План", "glp"), btn("◀️ Назад", "go", "goals")]))
    given = 0
    for goal_id, share in shares:
        goal = names[goal_id]
        given += share
        lines.append(f"{esc(label(goal['emoji'], goal['name']))} "
                     f"<b>+{money(profile, share)}</b> · {_fmt_percent(goal['percent'])}")
    lines += ["", f"💵 Остаётся свободными: <b>{money(profile, amount - given)}</b>"]
    return Screen("\n".join(lines), kb([btn("✅ Распределить", confirm.a, confirm.x, confirm.y)],
                                       [btn("✖️ Отмена", "go", "goals")]))


@router.callback_query(CB.filter(F.a == "gld"))
async def on_distribute(callback: CallbackQuery, state: FSMContext):
    await ask(callback, state, GoalS.distribute,
              "💰 <b>Распределить сумму по плану</b>\n\nКакую сумму раскладываем по копилкам?",
              back_to="goals")


@router.message(GoalS.distribute, TEXT)
async def on_distribute_amount(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    amount = parse_amount(message.text)
    if amount is None:
        await retry(message, state, "Не понял сумму. Пример: 50000 или 50к.")
        return
    screen = await preview_screen(repos, profile, amount, CB(a="gldo", x=str(amount), y="0"))
    await answer_in_place(message, state, screen)
    await state.clear()


@router.callback_query(CB.filter(F.a == "dist"))
async def on_distribute_income(callback: CallbackQuery, callback_data: CB,
                               repos: Repos, profile: Profile):
    tx = await repos.transactions.get(profile.id, int(callback_data.x or 0))
    if not tx or tx["kind"] != "income":
        await callback.answer("Доход не найден", show_alert=True)
        return
    if await repos.goals.is_distributed(profile.id, tx["id"]):
        await callback.answer("Этот доход уже распределён.", show_alert=True)
        return
    screen = await preview_screen(repos, profile, tx["base_amount"], CB(a="gldo", x=str(tx["base_amount"]),
                                                                   y=str(tx["id"])))
    await show(callback, screen)


@router.callback_query(CB.filter(F.a == "gldo"))
async def on_distribute_confirm(callback: CallbackQuery, callback_data: CB,
                                repos: Repos, profile: Profile):
    amount = int(callback_data.x or 0)
    tx_id = int(callback_data.y or 0) or None
    if amount <= 0:
        return
    if tx_id is not None:
        tx = await repos.transactions.get(profile.id, tx_id)
        if not tx or tx["kind"] != "income" or tx["base_amount"] != amount:
            await callback.answer("Доход не найден", show_alert=True)
            return
    before = {g["id"]: g for g in await repos.goals.list(profile.id)}
    shares = await repos.goals.distribute(profile.id, amount, profile.today, tx_id)
    if not shares:
        await callback.answer("Уже распределено или нет копилок с процентом.", show_alert=True)
        return
    after = {g["id"]: g for g in await repos.goals.list(profile.id)}
    given = sum(share for _, share in shares)
    lines = [f"✅ <b>Распределено {money(profile, given)}</b>", ""]
    reached = []
    for goal_id, share in shares:
        goal = after[goal_id]
        progress = (f" · {money(profile, goal['saved'])} из {money(profile, goal['target'])}"
                    if goal["target"] else f" · всего {money(profile, goal['saved'])}")
        lines.append(f"{esc(label(goal['emoji'], goal['name']))} <b>+{money(profile, share)}</b>{progress}")
        if goal["target"] and before[goal_id]["saved"] < goal["target"] <= goal["saved"]:
            reached.append(goal["name"])
    lines += ["", f"💵 Остаётся свободными: <b>{money(profile, amount - given)}</b>"]
    for name in reached:
        lines.append(f"🎉 Цель «{esc(name)}» достигнута!")
    screen = Screen("\n".join(lines), kb([btn("🎯 К копилкам", "go", "goals"), btn("🏠 Меню", "go", "menu")]))
    await show(callback, with_mascot(screen, profile, "goal_reached" if reached else "distribute"))
