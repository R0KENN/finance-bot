"""Статьи: писать, читать, принимать входящие статьи Telegram, отчёты-статьи."""
from __future__ import annotations

import math
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..repos import Repos
from ..repos.users import Profile
from ..services import article_markup as markup
from ..services import reports
from ..services.rich import send_article
from ..states import ArticleS
from ..ui.common import CB, TEXT, Screen, btn, kb, send, show
from ..ui.format import esc, pager
from ..ui.nav import section
from ..ui.prompts import answer_in_place, ask, retry

router = Router(name="articles")

PAGE = 6
MAX_BODY = 12000
ORIGIN_ICON = {"own": "✍️", "imported": "📥", "report": "📊"}


def _has_rich(message: Message) -> bool:
    return getattr(message, "rich_message", None) is not None


# ---------------------------------------------------------------- список

async def articles_screen(repos: Repos, profile: Profile, page: int = 0) -> Screen:
    total = await repos.articles.count(profile.id)
    pages = max(1, math.ceil(total / PAGE))
    page = max(0, min(page, pages - 1))
    items = await repos.articles.list(profile.id, PAGE, page * PAGE)
    lines = ["📰 <b>Статьи</b>", ""]
    if not items:
        lines += [
            "Здесь живут твои заметки и разборы: идеи по накоплению, финансовый план, "
            "итоги месяца. Статьи открываются в красивом формате Telegram — с заголовками, "
            "списками и таблицами.",
            "",
            "✍️ Напиши свою, 📥 перешли мне любую статью из Telegram или 📊 собери отчёт за месяц.",
        ]
    rows = []
    for item in items:
        stamp = item["updated_at"][:10]
        rows.append([btn(f"{ORIGIN_ICON.get(item['origin'], '📄')} {item['title'][:40]}", "arv", item["id"])])
        lines.append(f"{ORIGIN_ICON.get(item['origin'], '📄')} <b>{esc(item['title'])}</b> · {stamp}")
    nav = pager(page, pages, "arl")
    markup_rows = rows + ([nav] if nav else []) + [
        [btn("✍️ Написать", "arn"), btn("📊 Отчёт за месяц", "rep", "m")],
        [btn("ℹ️ Разметка", "arh"), btn("🏠 Меню", "go", "menu")],
    ]
    lines += ["", "<i>Перешли сюда статью Telegram — сохраню её сюда.</i>"] if items else []
    return Screen("\n".join(lines), kb(*markup_rows))


@section("articles")
async def articles_section(repos: Repos, profile: Profile) -> Screen:
    return await articles_screen(repos, profile)


@router.callback_query(CB.filter(F.a == "arl"))
async def on_list(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    page = int(callback_data.x) if callback_data.x.isdigit() else 0
    await show(callback, await articles_screen(repos, profile, page))


@router.callback_query(CB.filter(F.a == "arh"))
async def on_help(callback: CallbackQuery):
    await show(callback, Screen(markup.HELP, kb([btn("✍️ Написать", "arn")],
                                                [btn("◀️ Назад", "go", "articles")])))


# ---------------------------------------------------------------- чтение

def _article_kb(article_id: int):
    return kb(
        [btn("✏️ Править", "are", article_id), btn("🗑 Удалить", "ard", article_id)],
        [btn("📚 К списку", "go", "articles")],
    )


async def deliver(callback_or_message, repos: Repos, profile: Profile, article_id: int) -> bool:
    """Отправляет статью отдельным сообщением (Rich Message, а при отказе — HTML)."""
    article = await repos.articles.get(profile.id, article_id)
    if not article:
        return False
    bot = callback_or_message.bot
    await send_article(bot, profile.id, article["title"], article["body"], _article_kb(article_id))
    return True


@router.callback_query(CB.filter(F.a == "arv"))
async def on_view(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    if not await deliver(callback, repos, profile, int(callback_data.x or 0)):
        await callback.answer("Статья не найдена", show_alert=True)


# ---------------------------------------------------------------- написать

@router.callback_query(CB.filter(F.a == "arn"))
async def on_new(callback: CallbackQuery, state: FSMContext):
    await ask(callback, state, ArticleS.title,
              "✍️ <b>Новая статья</b>\n\nКак назовём?", back_to="articles")


@router.message(ArticleS.title, TEXT)
async def on_title(message: Message, state: FSMContext):
    title = message.text.strip()
    if not title or len(title) > 120:
        await retry(message, state, "Заголовок — от 1 до 120 символов.")
        return
    prompt = (f"✍️ <b>{esc(title)}</b>\n\nТеперь текст статьи одним сообщением. "
              "Можно списки, цитаты и таблицы:\n\n" + markup.HELP)
    await state.update_data(a_title=title, prompt=prompt)
    await state.set_state(ArticleS.body)
    await answer_in_place(message, state, Screen(prompt, kb([btn("✖️ Отмена", "go", "articles")])))


async def _body_from(message: Message) -> str | None:
    """Текст статьи из сообщения: обычный текст или пересланная статья Telegram."""
    if _has_rich(message):
        return markup.from_rich_message(message.rich_message)
    return message.text


@router.message(ArticleS.body, TEXT | F.func(_has_rich))
async def on_body(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    body = (await _body_from(message) or "").strip()
    if not body:
        await retry(message, state, "Текст пустой.")
        return
    if len(body) > MAX_BODY:
        await retry(message, state, f"Слишком длинно: максимум {MAX_BODY} символов.")
        return
    data = await state.get_data()
    article_id = await repos.articles.add(profile.id, data["a_title"], body)
    await answer_in_place(message, state, Screen(
        "✅ Статья сохранена.", kb([btn("📖 Открыть", "arv", article_id)],
                                  [btn("📚 К списку", "go", "articles")])))
    await state.clear()


# ---------------------------------------------------------------- править и удалить

@router.callback_query(CB.filter(F.a == "are"))
async def on_edit_menu(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    article = await repos.articles.get(profile.id, int(callback_data.x or 0))
    if not article:
        await callback.answer("Статья не найдена", show_alert=True)
        return
    # статья пришла отдельным сообщением — меню правки показываем под ней же
    await show(callback, Screen(
        f"✏️ <b>{esc(article['title'])}</b>\n\nЧто изменить?",
        kb([btn("🏷 Заголовок", "arf", "title", article["id"]),
            btn("📝 Заменить текст", "arf", "body", article["id"])],
           [btn("➕ Дописать", "arf", "append", article["id"])],
           [btn("◀️ Назад", "arv", article["id"])]),
    ))


@router.callback_query(CB.filter(F.a == "arf"))
async def on_edit_field(callback: CallbackQuery, callback_data: CB, state: FSMContext,
                        repos: Repos, profile: Profile):
    field, article_id = callback_data.x, int(callback_data.y or 0)
    if not await repos.articles.get(profile.id, article_id):
        await callback.answer("Статья не найдена", show_alert=True)
        return
    table = {
        "title": (ArticleS.edit_title, "🏷 Новый заголовок:"),
        "body": (ArticleS.edit_body, "📝 Новый текст целиком (старый будет заменён):"),
        "append": (ArticleS.append, "➕ Что дописать в конец?"),
    }
    if field not in table:
        return
    st, text = table[field]
    await ask(callback, state, st, text, back_to="articles", article_id=article_id)


async def _after_edit(message: Message, state: FSMContext, article_id: int) -> None:
    await answer_in_place(message, state, Screen(
        "✅ Сохранено.", kb([btn("📖 Открыть", "arv", article_id)],
                           [btn("📚 К списку", "go", "articles")])))
    await state.clear()


@router.message(ArticleS.edit_title, TEXT)
async def on_edit_title(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    title = message.text.strip()
    if not title or len(title) > 120:
        await retry(message, state, "Заголовок — от 1 до 120 символов.")
        return
    article_id = (await state.get_data())["article_id"]
    await repos.articles.update(profile.id, article_id, title=title)
    await _after_edit(message, state, article_id)


@router.message(ArticleS.edit_body, TEXT | F.func(_has_rich))
async def on_edit_body(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    body = (await _body_from(message) or "").strip()
    if not body or len(body) > MAX_BODY:
        await retry(message, state, f"Текст — от 1 до {MAX_BODY} символов.")
        return
    article_id = (await state.get_data())["article_id"]
    await repos.articles.update(profile.id, article_id, body=body)
    await _after_edit(message, state, article_id)


@router.message(ArticleS.append, TEXT | F.func(_has_rich))
async def on_append(message: Message, state: FSMContext, repos: Repos, profile: Profile):
    extra = (await _body_from(message) or "").strip()
    article_id = (await state.get_data())["article_id"]
    article = await repos.articles.get(profile.id, article_id)
    if not extra or not article or len(article["body"]) + len(extra) > MAX_BODY:
        await retry(message, state, "Пусто или статья станет слишком длинной.")
        return
    await repos.articles.update(profile.id, article_id, body=article["body"].rstrip() + "\n\n" + extra)
    await _after_edit(message, state, article_id)


@router.callback_query(CB.filter(F.a == "ard"))
async def on_delete_ask(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    article = await repos.articles.get(profile.id, int(callback_data.x or 0))
    if not article:
        await callback.answer("Статья не найдена", show_alert=True)
        return
    await show(callback, Screen(
        f"🗑 <b>Удалить «{esc(article['title'])}»?</b>",
        kb([btn("🗑 Да", "ardy", article["id"]), btn("◀️ Нет", "arv", article["id"])])))


@router.callback_query(CB.filter(F.a == "ardy"))
async def on_delete(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    await repos.articles.delete(profile.id, int(callback_data.x or 0))
    await show(callback, await articles_screen(repos, profile))


# ---------------------------------------------------------------- отчёт-статья

@router.callback_query(CB.filter(F.a == "rep"))
async def on_report(callback: CallbackQuery, callback_data: CB, repos: Repos, profile: Profile):
    key = callback_data.x or "m"
    if key == "m":
        key = f"{profile.today.year:04d}-{profile.today.month:02d}"
    title, body = await reports.month_report(repos, profile, key)
    article_id = await repos.articles.add(profile.id, title, body, origin="report")
    await callback.answer("Собираю отчёт…")
    await deliver(callback, repos, profile, article_id)


# ---------------------------------------------------------------- входящая статья

@router.message(StateFilter(None), F.func(_has_rich))
async def on_incoming_article(message: Message, repos: Repos, profile: Profile):
    """Пользователь прислал или переслал статью Telegram — сохраняем её у него."""
    text = markup.from_rich_message(message.rich_message)
    if not text:
        await message.answer("В этой статье я не нашёл текста.")
        return
    title, body = markup.split_title(text)
    article_id = await repos.articles.add(profile.id, title, body or text, origin="imported")
    await send(message.bot, message.chat.id, Screen(
        f"📥 <b>Статья сохранена</b>\n«{esc(title)}»",
        kb([btn("📖 Открыть", "arv", article_id)], [btn("📚 К списку", "go", "articles")])))
