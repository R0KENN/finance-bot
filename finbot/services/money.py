"""Суммы: разбор из текста, красивый вывод, полоски прогресса.

Деньги хранятся целыми числами в минимальных единицах валюты. У обычных валют это 1/100
(копейки, центы), у BTC и ETH — 1/10⁸. Количество знаков задаёт decimals_of(код).
"""
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

NBSP = " "

FIAT = {
    "RUB": ("₽", "Рубль"),
    "USD": ("$", "Доллар"),
    "EUR": ("€", "Евро"),
    "KZT": ("₸", "Тенге"),
    "UAH": ("₴", "Гривна"),
    "BYN": ("Br", "Бел. рубль"),
    "GEL": ("₾", "Лари"),
    "TRY": ("₺", "Лира"),
    "AMD": ("֏", "Драм"),
    "UZS": ("сўм", "Сум"),
}
CRYPTO = {
    "USDT": ("₮", "Tether USDT"),
    "USDC": ("USDC", "USD Coin"),
    "BTC": ("₿", "Bitcoin"),
    "ETH": ("Ξ", "Ethereum"),
}
CURRENCIES = {**FIAT, **CRYPTO}

# Знаков после запятой. Остальные валюты — 2.
DECIMALS = {"BTC": 8, "ETH": 8}
MAX_DECIMALS = max(DECIMALS.values())

MAX_MAJOR = Decimal(10) ** 10        # 10 млрд в основных единицах — защита от опечаток (и от переполнения int64)

_MULTIPLIERS = {
    "к": 1_000, "k": 1_000, "тыс": 1_000, "тысяч": 1_000,
    "м": 1_000_000, "m": 1_000_000, "млн": 1_000_000,
}
_AMOUNT_RE = re.compile(
    rf"^\s*(\d[\d\s ]*(?:[.,]\d{{1,{MAX_DECIMALS}}})?)\s*(тысяч|тыс|млн|к|k|м|m)?\.?\s*"
    r"(?:руб(?:лей|ля|\.)?|р\.?|₽|\$|€|₸|₴|usd|eur)?\s*$",
    re.IGNORECASE,
)


def symbol_of(code: str) -> str:
    return CURRENCIES.get(code, ("¤", code))[0]


def decimals_of(code: str) -> int:
    return DECIMALS.get(code, 2)


def is_crypto(code: str) -> bool:
    return code in CRYPTO


def parse_decimal(number: str, multiplier: int = 1) -> Decimal | None:
    """'1 500,5' -> Decimal('1500.5') в основных единицах. None для нуля, мусора и гигантов."""
    cleaned = re.sub(r"[\s ]", "", number).replace(",", ".")
    try:
        value = Decimal(cleaned) * multiplier
    except InvalidOperation:
        return None
    if value <= 0 or value > MAX_MAJOR:
        return None
    return value


def minor_of(value: Decimal, code: str) -> int | None:
    """Сумма в минимальных единицах валюты. None, если у суммы больше знаков, чем допускает валюта
    (0,001 ₽ или 0,123456789 BTC) — молча округлять деньги нельзя."""
    scaled = value * (Decimal(10) ** decimals_of(code))
    if scaled != scaled.to_integral_value():
        return None
    minor = int(scaled)
    return minor if minor > 0 else None


def rescale(minor: int, src: str, dst: str) -> int:
    """Та же сумма в единицах другой валюты (без курса!): нужна, когда знаков после запятой разное число."""
    shift = decimals_of(dst) - decimals_of(src)
    return int((Decimal(minor) * (Decimal(10) ** shift)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def to_minor(number: str, multiplier: int = 1, decimals: int = 2) -> int | None:
    """Совместимость: '1500,5' -> 150050 для валюты с двумя знаками."""
    value = parse_decimal(number, multiplier)
    if value is None:
        return None
    scaled = value * (Decimal(10) ** decimals)
    if scaled != scaled.to_integral_value():
        return None
    return int(scaled) if scaled > 0 else None


def parse_amount(text: str, code: str = "RUB") -> int | None:
    """Сумма из строки пользователя в минимальных единицах валюты `code`:
    '1 500', '1500,50', '2к', '1.5 млн'; для BTC — '0,00015'."""
    match = _AMOUNT_RE.match(text or "")
    if not match:
        return None
    number, suffix = match.groups()
    value = parse_decimal(number, _MULTIPLIERS.get((suffix or "").lower(), 1))
    return None if value is None else minor_of(value, code)


def parse_major(text: str) -> Decimal | None:
    """Сумма как число без привязки к валюте (валюту выберут позже). Допускает до 8 знаков."""
    match = _AMOUNT_RE.match(text or "")
    if not match:
        return None
    number, suffix = match.groups()
    return parse_decimal(number, _MULTIPLIERS.get((suffix or "").lower(), 1))


def format_major(value: Decimal) -> str:
    """Decimal('1500.50') -> '1 500,5': число без валюты, лишние нули убраны."""
    text = format(value.normalize(), "f")
    whole, _, frac = text.partition(".")
    whole = f"{int(whole):,}".replace(",", NBSP)
    return f"{whole},{frac}" if frac else whole


def fmt(minor: int, symbol: str = "₽", sign: bool = False, decimals: int = 2) -> str:
    """12345678 -> '123 456,78 ₽'. У обычных валют копейки показываются, только если они есть;
    у крипты хвостовые нули убираются (150000 при 8 знаках -> '0,0015')."""
    if minor < 0:
        prefix = "−"
    elif sign and minor > 0:
        prefix = "+"
    else:
        prefix = ""
    whole, frac = divmod(abs(minor), 10 ** decimals)
    body = f"{whole:,}".replace(",", NBSP)
    if frac:
        digits = str(frac).zfill(decimals)
        body += "," + (digits if decimals <= 2 else digits.rstrip("0"))
    return f"{prefix}{body}{NBSP}{symbol}"


def fmt_short(minor: int) -> str:
    """Компактно для осей графиков (только основная валюта, 2 знака): 1 250 000 -> '1,3 млн'."""
    value = abs(minor) / 100
    sign = "−" if minor < 0 else ""
    if value >= 1_000_000:
        return f"{sign}{value / 1_000_000:.1f}".replace(".", ",").replace(",0", "") + " млн"
    if value >= 10_000:
        return f"{sign}{value / 1_000:.0f} тыс"
    if value >= 1_000:
        return f"{sign}{value / 1_000:.1f}".replace(".", ",").replace(",0", "") + " тыс"
    return f"{sign}{value:.0f}"


def bar(ratio: float, width: int = 10) -> str:
    """▰▰▰▱▱▱ — полоска прогресса."""
    ratio = 0.0 if ratio != ratio else max(0.0, min(ratio, 1.0))
    filled = round(ratio * width)
    return "▰" * filled + "▱" * (width - filled)


def pct(part: int, whole: int) -> float:
    return part / whole * 100 if whole else 0.0
