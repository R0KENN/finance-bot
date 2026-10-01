"""Быстрый ввод одной строкой: «кофе 250», «+50000 зарплата», «такси 400 вчера»."""
import re
from dataclasses import dataclass
from decimal import Decimal
from datetime import date

from ..defaults import EXPENSE_KEYWORDS, INCOME_KEYWORDS
from .dates import parse_user_date
from .money import MAX_DECIMALS, minor_of, parse_decimal

_NUMBER = re.compile(
    rf"(?<![\w.,])(\d[\d ]*(?:[.,]\d{{1,{MAX_DECIMALS}}})?)\s*(тысяч|тыс|млн|к|k)?(?![\w])",
    re.IGNORECASE,
)
_DATE_TOKEN = re.compile(r"(?<![\d.])(\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?)(?![\d])")
_DAY_WORDS = re.compile(r"\b(позавчера|вчера|сегодня)\b", re.IGNORECASE)
_MULT = {"к": 1_000, "k": 1_000, "тыс": 1_000, "тысяч": 1_000, "млн": 1_000_000}

# Как пользователь пишет валюту в быстром вводе: «500$», «20 евро», «300 usd», «5000р»
CURRENCY_ALIASES = {
    "RUB": ["рублей", "рубля", "рубль", "руб", "р", "₽", "rub"],
    "USD": ["долларов", "доллара", "доллар", "долл", "баксов", "бакса", "бакс", "$", "usd"],
    "EUR": ["евро", "€", "eur"],
    "KZT": ["тенге", "₸", "kzt"],
    "UAH": ["гривен", "гривны", "гривна", "грн", "₴", "uah"],
    "BYN": ["byn"],
    "GEL": ["лари", "₾", "gel"],
    "TRY": ["лир", "лира", "₺", "try"],
    "AMD": ["драм", "драмов", "֏", "amd"],
    "UZS": ["сум", "сумов", "uzs"],
    "USDT": ["usdt", "тезер"],
    "USDC": ["usdc"],
    "BTC": ["btc", "bitcoin", "биткоин", "биткойн", "₿"],
    "ETH": ["eth", "ethereum", "эфириум", "эфир", "ξ"],
}
_ALIAS_TO_CODE = {alias: code for code, aliases in CURRENCY_ALIASES.items() for alias in aliases}
# Слово-валюта: не внутри другого слова (после цифры можно: «250р»), точка после сокращения съедается
_CURRENCY_WORD = re.compile(
    r"(?<![^\W\d_])(" + "|".join(sorted(map(re.escape, _ALIAS_TO_CODE), key=len, reverse=True))
    + r")(?![^\W\d_])\.?",
    re.IGNORECASE,
)


@dataclass
class QuickEntry:
    kind: str                 # income | expense
    amount: int | None        # в минимальных единицах валюты записи; None — слишком много знаков
    text: str                 # что осталось после вычета суммы и даты
    day: date | None = None
    currency: str | None = None   # код валюты, если пользователь её назвал
    value: Decimal | None = None  # сумма как число: в какую валюту её переводить, решит обработчик


def split_currency(text: str) -> tuple[str, str | None]:
    """'500 евро' -> ('500', 'EUR'); без валюты — (text, None)."""
    found = _CURRENCY_WORD.search(text or "")
    if not found:
        return text, None
    return _CURRENCY_WORD.sub(" ", text).strip(), _ALIAS_TO_CODE[found.group(1).lower()]


def parse_quick(raw: str, today: date) -> QuickEntry | None:
    text = (raw or "").strip()
    if not text or len(text) > 200:
        return None

    kind = "expense"
    if text[0] == "+":
        kind, text = "income", text[1:].strip()
    elif text[0] in "-−–—":
        text = text[1:].strip()

    day = None
    word = _DAY_WORDS.search(text)
    if word:
        day = parse_user_date(word.group(1), today)
        text = (text[:word.start()] + " " + text[word.end():])
    else:
        token = _DATE_TOKEN.search(text)
        if token:
            parsed = parse_user_date(token.group(1).replace("/", "."), today)
            if parsed:
                day = parsed
                text = text[:token.start()] + " " + text[token.end():]

    currency = None
    found = _CURRENCY_WORD.search(text)
    if found:
        currency = _ALIAS_TO_CODE[found.group(1).lower()]
        text = _CURRENCY_WORD.sub(" ", text)
    match = _NUMBER.search(text)
    if not match:
        return None
    value = parse_decimal(match.group(1), _MULT.get((match.group(2) or "").lower(), 1))
    if value is None:
        return None
    # Точность проверяем по названной валюте; если её нет — по обычной (2 знака), дальше решит обработчик
    amount = minor_of(value, currency or "RUB")
    rest = (text[:match.start()] + " " + text[match.end():])
    rest = re.sub(r"\s+", " ", rest).strip(" ,.;:-")
    return QuickEntry(kind=kind, amount=amount, value=value, text=rest, day=day, currency=currency)


def match_category(text: str, categories, kind: str):
    """Подбирает категорию пользователя по словам в строке. None, если не уверены."""
    haystack = f" {text.lower()} "
    if not text.strip():
        return None

    # 1. Прямое попадание названия (или его основы) в текст
    for cat in categories:
        name = cat["name"].lower()
        if name in haystack:
            return cat
        for word in re.findall(r"[а-яёa-z]{4,}", name):
            if word[:5] in haystack:
                return cat

    # 2. Словарь ключевых слов по стандартным названиям
    keywords = EXPENSE_KEYWORDS if kind == "expense" else INCOME_KEYWORDS
    by_name = {cat["name"]: cat for cat in categories}
    for name, stems in keywords.items():
        if name in by_name and any(stem in haystack for stem in stems):
            return by_name[name]
    return None


def match_source(text: str, sources):
    haystack = text.lower()
    if not haystack.strip():
        return None
    for src in sources:
        name = src["name"].lower()
        if name in haystack:
            return src
        for word in re.findall(r"[а-яёa-z0-9]{4,}", name):
            if word[:5] in haystack:
                return src
    return None
