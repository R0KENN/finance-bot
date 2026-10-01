from __future__ import annotations

from datetime import date

from .base import Repo


class BudgetsRepo(Repo):
    async def list(self, user_id: int):
        """Лимиты: общий (category_id IS NULL) идёт первым."""
        return await self.db.fetchall(
            "SELECT b.*, c.name AS cat_name, c.emoji AS cat_emoji "
            "FROM budgets b LEFT JOIN categories c ON c.id = b.category_id "
            "WHERE b.user_id = ? ORDER BY b.category_id IS NOT NULL, b.id",
            (user_id,),
        )

    async def get(self, user_id: int, budget_id: int):
        return await self.db.fetchone(
            "SELECT b.*, c.name AS cat_name, c.emoji AS cat_emoji "
            "FROM budgets b LEFT JOIN categories c ON c.id = b.category_id "
            "WHERE b.user_id = ? AND b.id = ?",
            (user_id, budget_id),
        )

    async def set_limit(self, user_id: int, category_id: int | None, amount: int) -> None:
        """Лимит на категорию (или общий, если category_id=None). Перезаписывает прежний."""
        if not await self.owns("categories", user_id, category_id):
            raise PermissionError("Категория чужая")
        if category_id is None:
            await self.db.execute(
                "DELETE FROM budgets WHERE user_id = ? AND category_id IS NULL", (user_id,)
            )
        else:
            await self.db.execute(
                "DELETE FROM budgets WHERE user_id = ? AND category_id = ?", (user_id, category_id)
            )
        await self.db.execute(
            "INSERT INTO budgets (user_id, category_id, amount) VALUES (?, ?, ?)",
            (user_id, category_id, amount),
        )

    async def delete(self, user_id: int, budget_id: int) -> None:
        await self.db.execute(
            "DELETE FROM budgets WHERE id = ? AND user_id = ?", (budget_id, user_id)
        )

    async def statuses(self, user_id: int, start: date, end: date):
        """Лимиты вместе с потраченным за период: [(строка лимита, потрачено)]."""
        rows = await self.list(user_id)
        result = []
        for row in rows:
            if row["category_id"] is None:
                spent = await self.db.scalar(
                    "SELECT SUM(base_amount) FROM transactions WHERE user_id = ? AND kind = 'expense' "
                    "AND day BETWEEN ? AND ?",
                    (user_id, start.isoformat(), end.isoformat()),
                )
            else:
                spent = await self.db.scalar(
                    "SELECT SUM(base_amount) FROM transactions WHERE user_id = ? AND kind = 'expense' "
                    "AND category_id = ? AND day BETWEEN ? AND ?",
                    (user_id, row["category_id"], start.isoformat(), end.isoformat()),
                )
            result.append((row, spent))
        return result
