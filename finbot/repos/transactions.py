from __future__ import annotations

from datetime import date

from .base import Repo, now_iso

_SELECT = """
    SELECT t.*,
           c.name AS cat_name, c.emoji AS cat_emoji, c.passive AS cat_passive,
           s.name AS src_name, s.emoji AS src_emoji,
           a.name AS acc_name, a.emoji AS acc_emoji, a.currency AS acc_currency,
           a2.name AS to_name, a2.emoji AS to_emoji, a2.currency AS to_currency
    FROM transactions t
    LEFT JOIN categories c ON c.id = t.category_id
    LEFT JOIN sources s ON s.id = t.source_id
    JOIN accounts a ON a.id = t.account_id
    LEFT JOIN accounts a2 ON a2.id = t.to_account_id
"""


class TransactionsRepo(Repo):
    def __init__(self, db, rates):
        super().__init__(db)
        self.rates = rates

    async def _currency(self, user_id: int, account_id: int) -> str:
        return await self.db.scalar(
            "SELECT currency FROM accounts WHERE id = ? AND user_id = ?", (account_id, user_id),
            default="RUB",
        )

    async def add(
        self,
        user_id: int,
        kind: str,
        amount: int,
        account_id: int,
        day: date,
        category_id: int | None = None,
        source_id: int | None = None,
        note: str = "",
        to_account_id: int | None = None,
        recurring_id: int | None = None,
        to_amount: int | None = None,
    ) -> int:
        """Новая операция. amount — в валюте счёта; в основную валюту переводим по курсу на сегодня
        и запоминаем, чтобы позже смена курса не меняла прошлую статистику.

        Для перевода между счетами в разных валютах to_amount — сколько пришло на второй счёт;
        если не указан, считаем по курсу.
        """
        if kind not in ("income", "expense", "transfer") or amount <= 0:
            raise ValueError("Некорректная операция")
        for table, row_id in (
            ("accounts", account_id), ("accounts", to_account_id),
            ("categories", category_id), ("sources", source_id),
        ):
            if not await self.owns(table, user_id, row_id):
                raise PermissionError(f"{table}#{row_id} чужой")
        currency = await self._currency(user_id, account_id)
        base_amount = await self.rates.to_base(user_id, amount, currency)
        stored_to = None
        if kind == "transfer" and to_account_id is not None:
            to_currency = await self._currency(user_id, to_account_id)
            if to_currency != currency:
                stored_to = to_amount or await self.rates.convert(user_id, amount, currency, to_currency)
        return await self.db.execute(
            "INSERT INTO transactions (user_id, kind, amount, base_amount, to_amount, account_id, "
            "to_account_id, category_id, source_id, note, day, created_at, recurring_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, kind, amount, base_amount, stored_to, account_id, to_account_id, category_id,
             source_id, note.strip()[:300], day.isoformat(), now_iso(), recurring_id),
        )

    async def get(self, user_id: int, tx_id: int):
        return await self.db.fetchone(
            _SELECT + " WHERE t.user_id = ? AND t.id = ?", (user_id, tx_id)
        )

    async def delete(self, user_id: int, tx_id: int) -> bool:
        found = await self.get(user_id, tx_id)
        if not found:
            return False
        await self.db.execute(
            "DELETE FROM transactions WHERE id = ? AND user_id = ?", (tx_id, user_id)
        )
        return True

    async def update(self, user_id: int, tx_id: int, **fields) -> None:
        allowed = {"amount", "category_id", "source_id", "note", "day", "account_id"}
        if not fields or set(fields) - allowed:
            raise ValueError("Нельзя менять эти поля")
        for table, key in (("categories", "category_id"), ("sources", "source_id"),
                           ("accounts", "account_id")):
            if key in fields and not await self.owns(table, user_id, fields[key]):
                raise PermissionError(f"{table}#{fields[key]} чужой")
        if "amount" in fields and fields["amount"] <= 0:
            raise ValueError("Сумма должна быть положительной")
        if "day" in fields and isinstance(fields["day"], date):
            fields["day"] = fields["day"].isoformat()
        if "amount" in fields or "account_id" in fields:
            current = await self.get(user_id, tx_id)
            if current is None:
                return
            amount = fields.get("amount", current["amount"])
            currency = await self._currency(user_id, fields.get("account_id", current["account_id"]))
            fields["base_amount"] = await self.rates.to_base(user_id, amount, currency)
            if current["to_amount"] is not None and "amount" in fields:
                # обмен валют: сохраняем курс, по которому менял пользователь
                fields["to_amount"] = round(current["to_amount"] * amount / current["amount"])
        columns = ", ".join(f"{name} = ?" for name in fields)
        await self.db.execute(
            f"UPDATE transactions SET {columns} WHERE id = ? AND user_id = ?",
            (*fields.values(), tx_id, user_id),
        )

    async def list(
        self, user_id: int, start: date, end: date, kind: str | None = None,
        category_id: int | None = None, search: str | None = None,
        limit: int = 8, offset: int = 0,
    ):
        where, params = self._filters(user_id, start, end, kind, category_id, search)
        return await self.db.fetchall(
            _SELECT + f" WHERE {where} ORDER BY t.day DESC, t.id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )

    async def count(
        self, user_id: int, start: date, end: date, kind: str | None = None,
        category_id: int | None = None, search: str | None = None,
    ) -> int:
        where, params = self._filters(user_id, start, end, kind, category_id, search)
        return await self.db.scalar(
            f"SELECT COUNT(*) FROM transactions t WHERE {where}", params
        )

    @staticmethod
    def _filters(user_id, start, end, kind, category_id, search):
        where = ["t.user_id = ?", "t.day BETWEEN ? AND ?"]
        params: list = [user_id, start.isoformat(), end.isoformat()]
        if kind:
            where.append("t.kind = ?")
            params.append(kind)
        if category_id:
            where.append("t.category_id = ?")
            params.append(category_id)
        if search:
            escaped = search.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append(
                "(pylower(t.note) LIKE ? ESCAPE '\\' OR t.category_id IN "
                "(SELECT id FROM categories WHERE user_id = t.user_id AND pylower(name) LIKE ? ESCAPE '\\') "
                "OR t.source_id IN "
                "(SELECT id FROM sources WHERE user_id = t.user_id AND pylower(name) LIKE ? ESCAPE '\\'))"
            )
            params += [f"%{escaped}%"] * 3
        return " AND ".join(where), params

    # ---------- агрегаты ----------

    async def totals(self, user_id: int, start: date, end: date) -> tuple[int, int]:
        rows = await self.db.fetchall(
            "SELECT kind, SUM(base_amount) AS total FROM transactions "
            "WHERE user_id = ? AND day BETWEEN ? AND ? AND kind IN ('income', 'expense') "
            "GROUP BY kind",
            (user_id, start.isoformat(), end.isoformat()),
        )
        sums = {row["kind"]: row["total"] for row in rows}
        return sums.get("income", 0), sums.get("expense", 0)

    async def by_category(self, user_id: int, kind: str, start: date, end: date):
        return await self.db.fetchall(
            "SELECT t.category_id AS id, COALESCE(c.name, 'Без категории') AS name, "
            "COALESCE(c.emoji, '📦') AS emoji, COALESCE(c.passive, 0) AS passive, "
            "SUM(t.base_amount) AS total, COUNT(*) AS n "
            "FROM transactions t LEFT JOIN categories c ON c.id = t.category_id "
            "WHERE t.user_id = ? AND t.kind = ? AND t.day BETWEEN ? AND ? "
            "GROUP BY t.category_id ORDER BY total DESC",
            (user_id, kind, start.isoformat(), end.isoformat()),
        )

    async def by_source(self, user_id: int, start: date, end: date):
        return await self.db.fetchall(
            "SELECT t.source_id AS id, COALESCE(s.name, 'Без источника') AS name, "
            "COALESCE(s.emoji, '🏷') AS emoji, SUM(t.base_amount) AS total, COUNT(*) AS n "
            "FROM transactions t LEFT JOIN sources s ON s.id = t.source_id "
            "WHERE t.user_id = ? AND t.kind = 'income' AND t.day BETWEEN ? AND ? "
            "GROUP BY t.source_id ORDER BY total DESC",
            (user_id, start.isoformat(), end.isoformat()),
        )

    async def monthly(self, user_id: int, start: date, end: date) -> dict[str, tuple[int, int]]:
        """{'2026-09': (доход, расход)} за период."""
        rows = await self.db.fetchall(
            "SELECT substr(day, 1, 7) AS m, kind, SUM(base_amount) AS total FROM transactions "
            "WHERE user_id = ? AND day BETWEEN ? AND ? AND kind IN ('income', 'expense') "
            "GROUP BY m, kind",
            (user_id, start.isoformat(), end.isoformat()),
        )
        out: dict[str, list[int]] = {}
        for row in rows:
            pair = out.setdefault(row["m"], [0, 0])
            pair[0 if row["kind"] == "income" else 1] = row["total"]
        return {month: (pair[0], pair[1]) for month, pair in out.items()}

    async def daily(self, user_id: int, kind: str, start: date, end: date) -> dict[str, int]:
        rows = await self.db.fetchall(
            "SELECT day, SUM(base_amount) AS total FROM transactions "
            "WHERE user_id = ? AND kind = ? AND day BETWEEN ? AND ? GROUP BY day",
            (user_id, kind, start.isoformat(), end.isoformat()),
        )
        return {row["day"]: row["total"] for row in rows}

    async def passive_income(self, user_id: int, start: date, end: date) -> int:
        return await self.db.scalar(
            "SELECT SUM(t.base_amount) FROM transactions t JOIN categories c ON c.id = t.category_id "
            "WHERE t.user_id = ? AND t.kind = 'income' AND c.passive = 1 AND t.day BETWEEN ? AND ?",
            (user_id, start.isoformat(), end.isoformat()),
        )

    async def spent_in_category(self, user_id: int, category_id: int, start: date, end: date) -> int:
        return await self.db.scalar(
            "SELECT SUM(base_amount) FROM transactions WHERE user_id = ? AND kind = 'expense' "
            "AND category_id = ? AND day BETWEEN ? AND ?",
            (user_id, category_id, start.isoformat(), end.isoformat()),
        )

    async def has_any_on(self, user_id: int, day: date) -> bool:
        return bool(await self.db.scalar(
            "SELECT COUNT(*) FROM transactions WHERE user_id = ? AND day = ?",
            (user_id, day.isoformat()),
        ))

    async def export_rows(self, user_id: int):
        return await self.db.fetchall(
            _SELECT + " WHERE t.user_id = ? ORDER BY t.day, t.id", (user_id,)
        )
