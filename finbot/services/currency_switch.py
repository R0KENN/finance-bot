"""Смена основной валюты: пересчёт всего, что хранится в основной валюте."""
from __future__ import annotations

from decimal import Decimal

from ..repos import Repos
from ..repos.users import Profile
from .money import FIAT, decimals_of
from .rates import RateUnknown


async def switch_base(repos: Repos, profile: Profile, new: str) -> None:
    """Переводит основную валюту пользователя на `new`.

    Пересчитываются по текущему курсу: сумма каждой операции в основной валюте, цели и
    накопления копилок, лимиты бюджетов, долги и свои курсы. Счета остаются в своих валютах.
    Всё выполняется одной транзакцией: при нехватке курса ничего не меняется (RateUnknown).
    """
    old = profile.currency
    if new == old:
        return
    if new not in FIAT:
        raise ValueError("Основной может быть только обычная валюта, не криптовалюта")
    uid = profile.id
    rates = repos.rates

    factor = await rates.rate(uid, old, new)
    if factor is None:
        raise RateUnknown(old, new)

    # Свои курсы выражены в старой основной валюте — переводим в новую
    own_new = {code: value * factor for code, value in (await rates.overrides(uid)).items() if code != new}

    # Курс каждой валюты счетов к новой основной: свой, по таблице ЦБ или через старую основную
    accounts = await repos.accounts.list(uid)
    per_currency: dict[str, Decimal] = {}
    for code in {a["currency"] for a in accounts}:
        if code == new:
            per_currency[code] = Decimal(1)
        elif code in own_new:
            per_currency[code] = own_new[code]
        else:
            a, b = await rates._rub(code), await rates._rub(new)
            if a is not None and b is not None:
                per_currency[code] = a / b
            elif code == old:
                per_currency[code] = factor
            else:
                raise RateUnknown(code, new)

    conn = repos.db.conn
    scale = float(factor)
    try:
        await conn.execute("UPDATE users SET currency = ? WHERE id = ?", (new, uid))
        await conn.execute("DELETE FROM user_rates WHERE user_id = ?", (uid,))
        for code, value in own_new.items():
            await conn.execute("INSERT INTO user_rates (user_id, code, rate) VALUES (?, ?, ?)",
                               (uid, code, float(value)))
        for code, rate in per_currency.items():
            await conn.execute(
                "UPDATE transactions SET base_amount = MAX(1, CAST(ROUND(amount * ?) AS INTEGER)) "
                "WHERE user_id = ? AND account_id IN "
                "(SELECT id FROM accounts WHERE user_id = ? AND currency = ?)",
                (float(rate) * 10 ** (2 - decimals_of(code)), uid, uid, code),
            )
        await conn.execute("UPDATE goals SET target = MAX(1, CAST(ROUND(target * ?) AS INTEGER)) "
                           "WHERE user_id = ? AND target IS NOT NULL", (scale, uid))
        await conn.execute("UPDATE goal_ops SET amount = CAST(ROUND(amount * ?) AS INTEGER) "
                           "WHERE user_id = ?", (scale, uid))
        await conn.execute("UPDATE budgets SET amount = MAX(1, CAST(ROUND(amount * ?) AS INTEGER)) "
                           "WHERE user_id = ?", (scale, uid))
        await conn.execute(
            "UPDATE debts SET amount = MAX(1, CAST(ROUND(amount * ?) AS INTEGER)), "
            "paid = CAST(ROUND(paid * ?) AS INTEGER) WHERE user_id = ?", (scale, scale, uid))
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    profile.currency = new
