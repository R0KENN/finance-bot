"""Выгрузка всех операций пользователя в CSV (открывается в Excel и Google Таблицах)."""
from __future__ import annotations

import csv
import io
from decimal import Decimal

from ..repos import Repos
from ..repos.users import Profile
from .money import decimals_of

HEADER = ["Дата", "Тип", "Сумма", "Валюта", "Сумма в основной валюте", "Основная валюта",
          "Категория", "Источник", "Счёт", "Куда (перевод)", "Получено (перевод)", "Заметка"]
KIND = {"income": "Доход", "expense": "Расход", "transfer": "Перевод"}


def _safe(value: str) -> str:
    """Защита от формул: ячейка, начинающаяся с = + - @, в Excel исполнится как формула."""
    value = value or ""
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


def _num(minor: int, code: str, negative: bool = False) -> str:
    """Сумма с точностью валюты: рубль — 2 знака, BTC — 8. Decimal, чтобы не терять точность."""
    digits = decimals_of(code)
    value = Decimal(-minor if negative else minor) / (Decimal(10) ** digits)
    return f"{value:.{digits}f}".replace(".", ",")


async def build_csv(repos: Repos, profile: Profile) -> bytes:
    rows = await repos.transactions.export_rows(profile.id)
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow(HEADER)
    for row in rows:
        expense = row["kind"] == "expense"
        received = ""
        if row["to_amount"] is not None:
            received = f"{_num(row['to_amount'], row['to_currency'])} {row['to_currency']}"
        writer.writerow([
            row["day"], KIND[row["kind"]], _num(row["amount"], row["acc_currency"], expense),
            row["acc_currency"],
            "" if row["kind"] == "transfer" else _num(row["base_amount"], profile.currency, expense),
            profile.currency,
            _safe(row["cat_name"]), _safe(row["src_name"]), _safe(row["acc_name"]),
            _safe(row["to_name"]), received, _safe(row["note"]),
        ])
    return out.getvalue().encode("utf-8-sig")   # BOM — чтобы Excel не ломал кириллицу
