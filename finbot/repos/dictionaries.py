"""Справочники пользователя: категории, источники дохода, счета."""
from __future__ import annotations

import sqlite3

from ..services.rates import RateUnknown
from .base import Repo


class CategoriesRepo(Repo):
    async def list(self, user_id: int, kind: str):
        return await self.db.fetchall(
            "SELECT * FROM categories WHERE user_id = ? AND kind = ? AND archived = 0 "
            "ORDER BY id",
            (user_id, kind),
        )

    async def get(self, user_id: int, category_id: int):
        return await self.db.fetchone(
            "SELECT * FROM categories WHERE id = ? AND user_id = ?", (category_id, user_id)
        )

    async def add(self, user_id: int, kind: str, name: str, emoji: str, passive: bool = False) -> int:
        """Новая категория. Если такая когда-то была и ушла в архив — воскрешаем."""
        old = await self.db.fetchone(
            "SELECT id FROM categories WHERE user_id = ? AND kind = ? AND name = ?",
            (user_id, kind, name),
        )
        if old:
            await self.db.execute(
                "UPDATE categories SET archived = 0, emoji = ? WHERE id = ?", (emoji, old["id"])
            )
            return old["id"]
        return await self.db.execute(
            "INSERT INTO categories (user_id, kind, name, emoji, passive) VALUES (?, ?, ?, ?, ?)",
            (user_id, kind, name, emoji, int(passive)),
        )

    async def rename(self, user_id: int, category_id: int, name: str, emoji: str) -> bool:
        try:
            await self.db.execute(
                "UPDATE categories SET name = ?, emoji = ? WHERE id = ? AND user_id = ?",
                (name, emoji, category_id, user_id),
            )
        except sqlite3.IntegrityError:
            return False
        return True

    async def toggle_passive(self, user_id: int, category_id: int) -> None:
        await self.db.execute(
            "UPDATE categories SET passive = 1 - passive WHERE id = ? AND user_id = ?",
            (category_id, user_id),
        )

    async def archive(self, user_id: int, category_id: int) -> None:
        """Категории с историей не удаляем, а прячем: операции остаются на месте."""
        await self.db.execute(
            "UPDATE categories SET archived = 1 WHERE id = ? AND user_id = ?",
            (category_id, user_id),
        )
        await self.db.execute(
            "DELETE FROM budgets WHERE category_id = ? AND user_id = ?", (category_id, user_id)
        )


class SourcesRepo(Repo):
    async def list(self, user_id: int):
        return await self.db.fetchall(
            "SELECT * FROM sources WHERE user_id = ? AND archived = 0 ORDER BY id", (user_id,)
        )

    async def get(self, user_id: int, source_id: int):
        return await self.db.fetchone(
            "SELECT * FROM sources WHERE id = ? AND user_id = ?", (source_id, user_id)
        )

    async def add(self, user_id: int, name: str, emoji: str = "🏷") -> int:
        old = await self.db.fetchone(
            "SELECT id FROM sources WHERE user_id = ? AND name = ?", (user_id, name)
        )
        if old:
            await self.db.execute(
                "UPDATE sources SET archived = 0, emoji = ? WHERE id = ?", (emoji, old["id"])
            )
            return old["id"]
        return await self.db.execute(
            "INSERT INTO sources (user_id, name, emoji) VALUES (?, ?, ?)", (user_id, name, emoji)
        )

    async def rename(self, user_id: int, source_id: int, name: str, emoji: str) -> bool:
        try:
            await self.db.execute(
                "UPDATE sources SET name = ?, emoji = ? WHERE id = ? AND user_id = ?",
                (name, emoji, source_id, user_id),
            )
        except sqlite3.IntegrityError:
            return False
        return True

    async def archive(self, user_id: int, source_id: int) -> None:
        await self.db.execute(
            "UPDATE sources SET archived = 1 WHERE id = ? AND user_id = ?", (source_id, user_id)
        )


class AccountsRepo(Repo):
    BALANCE_SQL = """
        SELECT a.*,
               a.initial
               + COALESCE((SELECT SUM(CASE t.kind WHEN 'income' THEN t.amount ELSE -t.amount END)
                           FROM transactions t WHERE t.account_id = a.id), 0)
               + COALESCE((SELECT SUM(COALESCE(t.to_amount, t.amount)) FROM transactions t
                           WHERE t.to_account_id = a.id AND t.kind = 'transfer'), 0) AS balance
        FROM accounts a
        WHERE a.user_id = ? AND a.archived = 0
        ORDER BY a.id
    """

    async def list(self, user_id: int):
        """Счета с вычисленным балансом."""
        return await self.db.fetchall(self.BALANCE_SQL, (user_id,))

    async def get(self, user_id: int, account_id: int):
        for row in await self.list(user_id):
            if row["id"] == account_id:
                return row
        return None

    def __init__(self, db, rates):
        super().__init__(db)
        self.rates = rates

    async def add(self, user_id: int, name: str, emoji: str = "💳", initial: int = 0,
                  currency: str | None = None) -> int:
        """Новый счёт. Без валюты — в основной валюте пользователя."""
        currency = currency or await self.rates._base(user_id)
        return await self.db.execute(
            "INSERT INTO accounts (user_id, name, emoji, initial, currency) VALUES (?, ?, ?, ?, ?)",
            (user_id, name, emoji, initial, currency),
        )

    async def set_all_currency(self, user_id: int, currency: str) -> None:
        """Только для нового пользователя без данных: все счета — в выбранной валюте."""
        await self.db.execute("UPDATE accounts SET currency = ? WHERE user_id = ?", (currency, user_id))

    async def rename(self, user_id: int, account_id: int, name: str, emoji: str) -> None:
        await self.db.execute(
            "UPDATE accounts SET name = ?, emoji = ? WHERE id = ? AND user_id = ?",
            (name, emoji, account_id, user_id),
        )

    async def set_initial(self, user_id: int, account_id: int, initial: int) -> None:
        await self.db.execute(
            "UPDATE accounts SET initial = ? WHERE id = ? AND user_id = ?",
            (initial, account_id, user_id),
        )

    async def archive(self, user_id: int, account_id: int) -> None:
        await self.db.execute(
            "UPDATE accounts SET archived = 1 WHERE id = ? AND user_id = ?", (account_id, user_id)
        )

    async def total(self, user_id: int) -> int:
        """Капитал в основной валюте: остатки всех счетов по текущим курсам."""
        total = 0
        for row in await self.list(user_id):
            try:
                total += await self.rates.to_base(user_id, row["balance"], row["currency"])
            except RateUnknown:
                continue          # курс пропал — счёт в капитал не входит, а не роняет экран
        return total

    async def by_currency(self, user_id: int) -> dict[str, int]:
        """{валюта: сумма остатков} — для показа «у меня 1 200 $ и 85 000 ₽»."""
        sums: dict[str, int] = {}
        for row in await self.list(user_id):
            sums[row["currency"]] = sums.get(row["currency"], 0) + row["balance"]
        return sums

    async def initial_total(self, user_id: int) -> int:
        """Стартовые остатки счетов в основной валюте."""
        total = 0
        for row in await self.list(user_id):
            try:
                total += await self.rates.to_base(user_id, row["initial"], row["currency"])
            except RateUnknown:
                continue
        return total
