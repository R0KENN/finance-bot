"""Мелкие помощники для текста экранов."""
from __future__ import annotations

import html
import re
from datetime import date

from ..repos.users import Profile
from ..services import dates
from ..services.money import decimals_of, fmt, symbol_of

_EMOJI_PREFIX = re.compile(r"^\s*([^\w\s]+)\s*(.*)$", re.UNICODE)


def esc(value) -> str:
    return html.escape(str(value or ""), quote=False)


def money(profile: Profile, minor: int, sign: bool = False) -> str:
    return fmt(minor, profile.symbol, sign)


def cmoney(code: str, minor: int, sign: bool = False) -> str:
    """Сумма в валюте с указанным кодом: 'USD' -> '1 200 $'."""
    return fmt(minor, symbol_of(code), sign, decimals_of(code))


def tx_amount(row, profile: Profile, sign: bool = False) -> str:
    """Сумма операции в валюте её счёта; для чужой валюты — с пересчётом в основную."""
    text = cmoney(row["acc_currency"], row["amount"], sign)
    if row["acc_currency"] != profile.currency and row["kind"] != "transfer":
        text += f" (≈{money(profile, row['base_amount'])})"
    return text


def split_emoji(text: str, default: str) -> tuple[str, str]:
    """'🛒 Продукты' -> ('🛒', 'Продукты'); 'Продукты' -> (default, 'Продукты')."""
    text = (text or "").strip()
    match = _EMOJI_PREFIX.match(text)
    if match and match.group(2):
        return match.group(1)[:8], match.group(2).strip()
    return default, text


def valid_name(name: str) -> bool:
    return 1 <= len(name) <= 40 and any(ch.isalnum() for ch in name)


def label(emoji: str | None, name: str | None) -> str:
    return f"{emoji or ''} {name or ''}".strip()


def cat_label(row) -> str:
    return label(row["cat_emoji"], row["cat_name"]) if row["cat_name"] else "📦 Без категории"


def kind_title(kind: str) -> str:
    return {"income": "Доход", "expense": "Расход", "transfer": "Перевод"}[kind]


def kind_icon(kind: str) -> str:
    return {"income": "➕", "expense": "➖", "transfer": "🔄"}[kind]


def _transfer_amount(row) -> str:
    """'1 000 $' или, при обмене валют, '1 000 $ → 90 000 ₽'."""
    sent = cmoney(row["acc_currency"], row["amount"])
    if row["to_amount"] is not None:
        return f"{sent} → {cmoney(row['to_currency'], row['to_amount'])}"
    return sent


def tx_line(row, profile: Profile, today: date | None = None, with_day: bool = True) -> str:
    """Одна строка истории: «15 сен  🛒 Продукты  −1 250 ₽»."""
    today = today or profile.today
    day = dates.fmt_day(date.fromisoformat(row["day"]), today) if with_day else ""
    if row["kind"] == "transfer":
        what = f"🔄 {esc(row['acc_name'])} → {esc(row['to_name'])}"
        amount = _transfer_amount(row)
    else:
        what = esc(cat_label(row))
        if row["kind"] == "income" and row["src_name"]:
            what += f" · {esc(row['src_name'])}"
        amount = cmoney(row["acc_currency"], row["amount"] if row["kind"] == "income" else -row["amount"],
                        sign=row["kind"] == "income")
    prefix = f"<code>{day:<9}</code> " if with_day else ""
    return f"{prefix}{what} <b>{amount}</b>"


def tx_card(row, profile: Profile) -> str:
    today = profile.today
    amount = _transfer_amount(row) if row["kind"] == "transfer" else tx_amount(row, profile)
    lines = [f"{kind_icon(row['kind'])} <b>{kind_title(row['kind'])} · {amount}</b>", ""]
    if row["kind"] == "transfer":
        lines.append(f"🔄 {esc(row['acc_name'])} → {esc(row['to_name'])}")
    else:
        lines.append(f"🏷 Категория: {esc(cat_label(row))}")
        if row["kind"] == "income":
            lines.append(f"🧾 Источник: {esc(row['src_name']) if row['src_name'] else '—'}")
        lines.append(f"👛 Счёт: {esc(label(row['acc_emoji'], row['acc_name']))}")
    lines.append(f"📅 Дата: {dates.fmt_day(date.fromisoformat(row['day']), today)}")
    if row["note"]:
        lines.append(f"📝 {esc(row['note'])}")
    return "\n".join(lines)


def pager(page: int, pages: int, a: str, *args) -> list:
    """Кнопки ◀ 2/5 ▶ для постраничных списков; None-значений не бывает."""
    from ..ui.common import btn
    row = []
    if page > 0:
        row.append(btn("◀️", a, *args, page - 1))
    row.append(btn(f"{page + 1}/{pages}", "noop"))
    if page < pages - 1:
        row.append(btn("▶️", a, *args, page + 1))
    return row if pages > 1 else []
