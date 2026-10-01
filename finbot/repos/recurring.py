from __future__ import annotations

from datetime import date

from .base import Repo

_SELECT = """
    SELECT r.*, c.name AS cat_name, c.emoji AS cat_emoji, s.name AS src_name,
           a.name AS acc_name, a.emoji AS acc_emoji, a.currency AS acc_currency
    FROM recurring r
    LEFT JOIN categories c ON c.id = r.category_id
    LEFT JOIN sources s ON s.id = r.source_id
    JOIN accounts a ON a.id = r.account_id
"""


class RecurringRepo(Repo):
    async def list(self, user_id: int):
        return await self.db.fetchall(_SELECT + " WHERE r.user_id = ? ORDER BY r.id", (user_id,))

    async def get(self, user_id: int, rec_id: int):
        return await self.db.fetchone(
            _SELECT + " WHERE r.user_id = ? AND r.id = ?", (user_id, rec_id)
        )

    async def add(
        self, user_id: int, kind: str, amount: int, account_id: int, freq: str, param: int,
        next_day: date, category_id: int | None = None, source_id: int | None = None,
        note: str = "",
    ) -> int:
        for table, row_id in (("accounts", account_id), ("categories", category_id),
                              ("sources", source_id)):
            if not await self.owns(table, user_id, row_id):
                raise PermissionError(f"{table}#{row_id} чужой")
        return await self.db.execute(
            "INSERT INTO recurring (user_id, kind, amount, category_id, source_id, account_id, "
            "note, freq, param, next_day) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, kind, amount, category_id, source_id, account_id, note[:200], freq, param,
             next_day.isoformat()),
        )

    async def toggle(self, user_id: int, rec_id: int) -> None:
        await self.db.execute(
            "UPDATE recurring SET active = 1 - active WHERE id = ? AND user_id = ?",
            (rec_id, user_id),
        )

    async def delete(self, user_id: int, rec_id: int) -> None:
        await self.db.execute("DELETE FROM recurring WHERE id = ? AND user_id = ?", (rec_id, user_id))

    async def due(self, user_id: int, today: date):
        return await self.db.fetchall(
            _SELECT + " WHERE r.user_id = ? AND r.active = 1 AND r.next_day <= ? ORDER BY r.next_day",
            (user_id, today.isoformat()),
        )

    async def set_next(self, user_id: int, rec_id: int, next_day: date) -> None:
        await self.db.execute(
            "UPDATE recurring SET next_day = ? WHERE id = ? AND user_id = ?",
            (next_day.isoformat(), rec_id, user_id),
        )
