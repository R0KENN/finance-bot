"""Справочники: категории расходов, виды доходов, источники дохода."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..repos import Repos
from ..repos.users import Profile
from ..states import DictS
from ..ui.common import CB, TEXT, Screen, btn, grid, kb, show
from ..ui.format import esc, label, split_emoji, valid_name
from ..ui.nav import section
from ..ui.prompts import answer_in_place, ask, retry

router = Router(name="categories")

# Тип справочника в callback: e — категории расходов, i — виды доходов, s — источники
TITLES = {"e": "📉 Категории расходов", "i": "📈 Виды доходов", "s": "🧾 Источники дохода"}
KIND = {"e": "expense", "i": "income"}


@section("dicts")
async def dicts_screen(repos: Repos, profile: Profile) -> Screen:
    text = ("🗂 <b>Справочники</b>\n\n"
            "📉 <b>Категории расходов</b> — на что тратишь\n"
            "📈 <b>Виды доходов</b> — зарплата, фриланс, инвестиции…\n"
            "🧾 <b>Источники</b> — кто или что приносит деньги: работодатель, клиент, площадка")
    markup = kb([btn(TITLES["e"], "dl", "e")], [btn(TITLES["i"], "dl", "i")],
                [btn(TITLES["s"], "dl", "s")], [btn("◀️ Назад", "go", "settings")])
    return Screen(text, markup)


async def _items(repos: Repos, uid: int, typ: str):
    if typ == "s":
        return await repos.sources.list(uid)
    return await repos.categories.list(uid, KIND[typ])


async def list_screen(repos: Repos, profile: Profile, typ: str) -> Screen:
    items = await _items(repos, profile.id, typ)
    lines = [f"<b>{TITLES[typ]}</b>", ""]
    if typ == "i":
        lines.append("🌱 — пассивный доход (считается отдельно в статистике)\n")
    if not items:
        lines.append("Пока пусто.")
    buttons = []
    for item in items:
        passive = " 🌱" if typ == "i" and item["passive"] else ""
        buttons.append(btn(label(item["emoji"], item["name"]) + passive, "dd", typ, item["id"]))
    markup = kb(*grid(buttons, 2), [btn("➕ Добавить", "da", typ)], [btn("◀️ Назад", "go", "dicts")])
    return Screen("\n".join(lines), markup)


@router.callback_query(CB.filter(F.a == "dl"))
async def on_list(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    if callback_data.x in TITLES:
        await show(callback, await list_screen(repos, profile, callback_data.x))


async def _get(repos: Repos, uid: int, typ: str, item_id: int):
    return await (repos.sources.get(uid, item_id) if typ == "s" else repos.categories.get(uid, item_id))


@router.callback_query(CB.filter(F.a == "dd"))
async def on_item(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    typ, item_id = callback_data.x, int(callback_data.y or 0)
    item = await _get(repos, profile.id, typ, item_id) if typ in TITLES else None
    if not item or item["archived"]:
        await show(callback, await dicts_screen(repos, profile))
        return
    rows = [[btn("✏️ Переименовать", "dr", typ, item_id)]]
    if typ == "i":
        rows.append([btn("🌱 Не пассивный" if item["passive"] else "🌱 Пассивный доход",
                         "dp", item_id)])
    rows.append([btn("🗑 Удалить", "dx", typ, item_id)])
    rows.append([btn("◀️ Назад", "dl", typ)])
    await show(callback, Screen(f"{esc(label(item['emoji'], item['name']))}", kb(*rows)))


@router.callback_query(CB.filter(F.a == "dp"))
async def on_passive(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    await repos.categories.toggle_passive(profile.id, int(callback_data.x or 0))
    await show(callback, await list_screen(repos, profile, "i"))


@router.callback_query(CB.filter(F.a == "dx"))
async def on_delete(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    typ, item_id = callback_data.x, int(callback_data.y or 0)
    if typ not in TITLES:
        return
    if typ == "s":
        await repos.sources.archive(profile.id, item_id)
    else:
        remaining = await repos.categories.list(profile.id, KIND[typ])
        if len(remaining) <= 1:
            await callback.answer("Нужна хотя бы одна категория.", show_alert=True)
            return
        await repos.categories.archive(profile.id, item_id)
    await show(callback, await list_screen(repos, profile, typ))


@router.callback_query(CB.filter(F.a == "da"))
async def on_add(callback: CallbackQuery, callback_data: CB, state: FSMContext):
    typ = callback_data.x
    if typ not in TITLES:
        return
    await ask(callback, state, DictS.add,
              f"{TITLES[typ]}\n\nНапиши название, можно с эмодзи: <code>🎣 Рыбалка</code>",
              back_to="dicts", typ=typ)


@router.message(DictS.add, TEXT)
async def on_add_name(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    typ = (await state.get_data())["typ"]
    emoji, name = split_emoji(message.text, "🏷")
    if not valid_name(name):
        await retry(message, state, "Название от 1 до 40 символов.")
        return
    if typ == "s":
        await repos.sources.add(profile.id, name, emoji)
    else:
        await repos.categories.add(profile.id, KIND[typ], name, emoji)
    await answer_in_place(message, state, await list_screen(repos, profile, typ))
    await state.clear()


@router.callback_query(CB.filter(F.a == "dr"))
async def on_rename(callback: CallbackQuery, callback_data: CB, state: FSMContext):
    typ, item_id = callback_data.x, int(callback_data.y or 0)
    if typ not in TITLES:
        return
    await ask(callback, state, DictS.rename, "✏️ Новое название (можно с эмодзи):",
              back_to="dicts", typ=typ, item_id=item_id)


@router.message(DictS.rename, TEXT)
async def on_rename_text(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    data = await state.get_data()
    typ, item_id = data["typ"], data["item_id"]
    emoji, name = split_emoji(message.text, "🏷")
    if not valid_name(name):
        await retry(message, state, "Название от 1 до 40 символов.")
        return
    repo = repos.sources if typ == "s" else repos.categories
    if not await repo.rename(profile.id, item_id, name, emoji):
        await retry(message, state, "Такое название уже есть.")
        return
    await answer_in_place(message, state, await list_screen(repos, profile, typ))
    await state.clear()
