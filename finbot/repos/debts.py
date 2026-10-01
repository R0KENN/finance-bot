from __future__ import annotations

from datetime import date

from .base import Repo, now_iso


class DebtsRepo(Repo):
    """Учёт долгов. Он отдельный от счетов: движение денег записывай обычной операцией."""

    async def list(self, user_id: int, closed: bool = False):
        return await self.db.fetchall(
            "SELECT * FROM debts WHERE user_id = ? AND closed = ? "
            "ORDER BY due_day IS NULL, due_day, id",
            (user_id, int(closed)),
        )

    async def get(self, user_id: int, debt_id: int):
        return await self.db.fetchone(
            "SELECT * FROM debts WHERE id = ? AND user_id = ?", (debt_id, user_id)
        )

    async def add(
        self, user_id: int, direction: str, person: str, amount: int,
        due_day: date | None = None, note: str = "",
    ) -> int:
        if direction not in ("owe", "owed") or amount <= 0:
            raise ValueError("Некорректный долг")
        return await self.db.execute(
            "INSERT INTO debts (user_id, direction, person, amount, due_day, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, direction, person.strip()[:60], amount,
             due_day.isoformat() if due_day else None, note[:200], now_iso()),
        )

    async def pay(self, user_id: int, debt_id: int, amount: int) -> bool:
        """Частичное погашение. Нельзя погасить больше остатка; полное погашение закрывает долг."""
        debt = await self.get(user_id, debt_id)
        if not debt or debt["closed"] or amount <= 0 or amount > debt["amount"] - debt["paid"]:
            return False
        paid = debt["paid"] + amount
        await self.db.execute(
            "UPDATE debts SET paid = ?, closed = ? WHERE id = ? AND user_id = ?",
            (paid, int(paid >= debt["amount"]), debt_id, user_id),
        )
        return True

    async def close(self, user_id: int, debt_id: int) -> None:
        await self.db.execute(
            "UPDATE debts SET paid = amount, closed = 1 WHERE id = ? AND user_id = ?",
            (debt_id, user_id),
        )

    async def delete(self, user_id: int, debt_id: int) -> None:
        await self.db.execute("DELETE FROM debts WHERE id = ? AND user_id = ?", (debt_id, user_id))

    async def remaining(self, user_id: int) -> tuple[int, int]:
        """(сколько я должен, сколько должны мне) — по открытым долгам."""
        rows = await self.db.fetchall(
            "SELECT direction, SUM(amount - paid) AS remaining FROM debts "
            "WHERE user_id = ? AND closed = 0 GROUP BY direction",
            (user_id,),
        )
        sums = {row["direction"]: row["remaining"] for row in rows}
        return sums.get("owe", 0), sums.get("owed", 0)

    async def due_on(self, user_id: int, day: date):
        return await self.db.fetchall(
            "SELECT * FROM debts WHERE user_id = ? AND closed = 0 AND due_day = ?",
            (user_id, day.isoformat()),
        )
