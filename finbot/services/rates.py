"""Курсы валют и конвертация.

Обычные валюты берём у ЦБ РФ, криптовалюты (USDT, USDC, BTC, ETH) — у CoinGecko, а если он
недоступен, у Coinbase. Все курсы даны к рублю, любые пары считаем через рубль.
Пользователь может задать свой курс к основной валюте — он главнее автоматического.
Сеть нужна только для обновления курсов; при записи операций работаем с сохранённой таблицей.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Awaitable, Callable

from ..db import Database
from .money import CRYPTO, CURRENCIES, decimals_of

log = logging.getLogger(__name__)

SOURCES = [
    "https://www.cbr-xml-daily.ru/daily_json.js",
    "https://www.cbr-xml-daily.com/daily_json.js",
]
REFRESH_EVERY = timedelta(hours=1)          # крипта меняется быстро, а запрос дешёвый
RETRY_AFTER_FAIL = timedelta(minutes=10)

Fetcher = Callable[[], Awaitable[dict[str, float]]]


class RateUnknown(Exception):
    """Нет курса между двумя валютами: ни автоматического, ни заданного пользователем."""

    def __init__(self, src: str, dst: str):
        super().__init__(f"Нет курса {src} → {dst}")
        self.src, self.dst = src, dst


async def fetch_cbr() -> dict[str, float]:
    """{код: сколько рублей за единицу}. Бросает исключение при любой сетевой ошибке."""
    import aiohttp

    last_error: Exception | None = None
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for url in SOURCES:
            try:
                async with session.get(url) as response:
                    response.raise_for_status()
                    data = await response.json(content_type=None)
                rates = {"RUB": 1.0}
                for code, item in data["Valute"].items():
                    rates[code] = float(item["Value"]) / float(item["Nominal"])
                return rates
            except Exception as error:  # noqa: BLE001 — пробуем следующий источник
                last_error = error
    raise RuntimeError(f"Курсы недоступны: {last_error}")


COINGECKO_IDS = {"USDT": "tether", "USDC": "usd-coin", "BTC": "bitcoin", "ETH": "ethereum"}
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
COINBASE_URL = "https://api.coinbase.com/v2/exchange-rates"


async def fetch_crypto() -> dict[str, float]:
    """{код: сколько рублей за единицу} для криптовалют. Сначала CoinGecko (один запрос на все),
    недостающее добираем у Coinbase. Бросает исключение, если не получили ничего."""
    import aiohttp

    rates: dict[str, float] = {}
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            params = {"ids": ",".join(COINGECKO_IDS.values()), "vs_currencies": "rub"}
            async with session.get(COINGECKO_URL, params=params) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)
            for code, coin in COINGECKO_IDS.items():
                value = data.get(coin, {}).get("rub")
                if value:
                    rates[code] = float(value)
        except Exception as error:  # noqa: BLE001 — запасной источник ниже
            log.info("CoinGecko недоступен: %s", error)
        for code in CRYPTO:
            if code in rates:
                continue
            try:
                async with session.get(COINBASE_URL, params={"currency": code}) as response:
                    response.raise_for_status()
                    data = await response.json(content_type=None)
                rates[code] = float(data["data"]["rates"]["RUB"])
            except Exception as error:  # noqa: BLE001
                log.info("Coinbase не дал курс %s: %s", code, error)
    if not rates:
        raise RuntimeError("Курсы криптовалют недоступны")
    return rates


async def fetch_all() -> dict[str, float]:
    """Все курсы: обычные валюты и криптовалюты. Достаточно, чтобы получилась хотя бы одна часть."""
    results: dict[str, float] = {}
    errors = []
    for fetch in (fetch_cbr, fetch_crypto):
        try:
            results.update(await fetch())
        except Exception as error:  # noqa: BLE001
            errors.append(str(error))
    if not results:
        raise RuntimeError("; ".join(errors))
    return results


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class RatesService:
    def __init__(self, db: Database, fetcher: Fetcher = fetch_all):
        self.db = db
        self.fetcher = fetcher
        self._next_attempt = datetime.min.replace(tzinfo=timezone.utc)

    # ---------------------------------------------------------------- таблица

    async def set_table(self, rub_per_unit: dict[str, float]) -> None:
        """Сохраняет курсы (только известные боту валюты) и время обновления."""
        stamp = _stamp(_now())
        rows = [(code, value, stamp) for code, value in rub_per_unit.items()
                if code in CURRENCIES and value > 0]
        if rows:
            await self.db.executemany(
                "INSERT INTO rates (code, rub, updated) VALUES (?, ?, ?) "
                "ON CONFLICT(code) DO UPDATE SET rub = excluded.rub, updated = excluded.updated",
                rows,
            )

    async def refresh(self) -> bool:
        """Обновляет курсы из сети. False — не удалось (остаются прежние)."""
        try:
            await self.set_table(await self.fetcher())
        except Exception as error:  # noqa: BLE001
            log.warning("Не удалось обновить курсы: %s", error)
            self._next_attempt = _now() + RETRY_AFTER_FAIL
            return False
        self._next_attempt = _now() + REFRESH_EVERY
        return True

    async def refresh_if_stale(self) -> None:
        if _now() >= self._next_attempt:
            await self.refresh()

    async def last_update(self) -> datetime | None:
        value = await self.db.scalar("SELECT MAX(updated) FROM rates", default=None)
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if value else None

    async def _rub(self, code: str) -> Decimal | None:
        if code == "RUB":
            return Decimal(1)
        row = await self.db.fetchone("SELECT rub FROM rates WHERE code = ?", (code,))
        return Decimal(str(row["rub"])) if row else None

    # ---------------------------------------------------------------- свои курсы

    async def overrides(self, user_id: int) -> dict[str, Decimal]:
        rows = await self.db.fetchall("SELECT code, rate FROM user_rates WHERE user_id = ?", (user_id,))
        return {row["code"]: Decimal(str(row["rate"])) for row in rows}

    async def set_override(self, user_id: int, code: str, rate: Decimal) -> None:
        if rate <= 0 or code not in CURRENCIES:
            raise ValueError("Некорректный курс")
        await self.db.execute(
            "INSERT INTO user_rates (user_id, code, rate) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, code) DO UPDATE SET rate = excluded.rate",
            (user_id, code, float(rate)),
        )

    async def clear_override(self, user_id: int, code: str) -> None:
        await self.db.execute("DELETE FROM user_rates WHERE user_id = ? AND code = ?", (user_id, code))

    async def _base(self, user_id: int) -> str:
        return await self.db.scalar("SELECT currency FROM users WHERE id = ?", (user_id,), default="RUB")

    # ---------------------------------------------------------------- конвертация

    async def rate(self, user_id: int, src: str, dst: str) -> Decimal | None:
        """Сколько единиц dst за одну единицу src. None, если курса нет."""
        if src == dst:
            return Decimal(1)
        base = await self._base(user_id)
        own = await self.overrides(user_id)
        if dst == base and src in own:
            return own[src]
        if src == base and dst in own:
            return Decimal(1) / own[dst]
        a, b = await self._rub(src), await self._rub(dst)
        if a is None or b is None:
            return None
        return a / b

    async def auto_rate(self, src: str, dst: str) -> Decimal | None:
        """Курс только по таблице ЦБ, без своих курсов пользователя."""
        a, b = await self._rub(src), await self._rub(dst)
        return None if a is None or b is None else a / b

    async def has_rate(self, user_id: int, code: str) -> bool:
        """Можно ли переводить валюту `code` в основную валюту пользователя."""
        return await self.rate(user_id, code, await self._base(user_id)) is not None

    async def convert(self, user_id: int, amount: int, src: str, dst: str) -> int:
        """Сумма в минимальных единицах из src в dst. RateUnknown, если курса нет."""
        if src == dst:
            return amount
        rate = await self.rate(user_id, src, dst)
        if rate is None:
            raise RateUnknown(src, dst)
        # курс — за целую единицу, а суммы хранятся в долях (копейки, сатоши), поэтому поправка на знаки
        shift = Decimal(10) ** (decimals_of(dst) - decimals_of(src))
        return int((Decimal(amount) * rate * shift).quantize(Decimal(1), rounding=ROUND_HALF_UP))

    async def to_base(self, user_id: int, amount: int, src: str) -> int:
        return await self.convert(user_id, amount, src, await self._base(user_id))

    async def describe(self, user_id: int, code: str) -> tuple[Decimal | None, str]:
        """(курс к основной валюте, 'свой' | 'авто' | '')."""
        base = await self._base(user_id)
        own = await self.overrides(user_id)
        if code in own and code != base:
            return own[code], "свой"
        rate = await self.rate(user_id, code, base)
        return rate, ("авто" if rate is not None else "")
