"""Копилки (цели) и план распределения доходов."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from .base import Repo, now_iso

_LIST = """
    SELECT g.*, COALESCE((SELECT SUM(o.amount) FROM goal_ops o WHERE o.goal_id = g.id), 0) AS saved
    FROM goals g WHERE g.user_id = ?
"""


def split_by_plan(amount: int, goals) -> list[tuple[int, int]]:
    """Делит сумму по процентам плана. Остаток от округления остаётся свободным.

    Возвращает [(goal_id, сумма)] только для копилок с процентом > 0.
    """
    shares = []
    for goal in goals:
        if goal["percent"] and goal["percent"] > 0:
            share = int(amount * Decimal(str(goal["percent"])) / 100)
            if share > 0:
                shares.append((goal["id"], share))
    return shares


class GoalsRepo(Repo):
    async def list(self, user_id: int):
        return await self.db.fetchall(_LIST + " ORDER BY g.id", (user_id,))

    async def get(self, user_id: int, goal_id: int):
        return await self.db.fetchone(_LIST + " AND g.id = ?", (user_id, goal_id))

    async def add(
        self, user_id: int, name: str, emoji: str = "🎯",
        target: int | None = None, percent: float = 0,
    ) -> int:
        return await self.db.execute(
            "INSERT INTO goals (user_id, name, emoji, target, percent) VALUES (?, ?, ?, ?, ?)",
            (user_id, name, emoji, target, percent),
        )

    async def update(self, user_id: int, goal_id: int, **fields) -> None:
        allowed = {"name", "emoji", "target", "percent"}
        if not fields or set(fields) - allowed:
            raise ValueError("Нельзя менять эти поля")
        columns = ", ".join(f"{name} = ?" for name in fields)
        await self.db.execute(
            f"UPDATE goals SET {columns} WHERE id = ? AND user_id = ?",
            (*fields.values(), goal_id, user_id),
        )

    async def delete(self, user_id: int, goal_id: int) -> None:
        """Удаляет копилку. Её деньги возвращаются в свободные — со счетов они не уходили."""
        await self.db.execute("DELETE FROM goals WHERE id = ? AND user_id = ?", (goal_id, user_id))

    async def add_op(
        self, user_id: int, goal_id: int, amount: int, day: date, note: str = "",
        tx_id: int | None = None,
    ) -> bool:
        goal = await self.get(user_id, goal_id)
        if not goal or amount == 0:
            return False
        if goal["saved"] + amount < 0:
            return False
        await self.db.execute(
            "INSERT INTO goal_ops (user_id, goal_id, amount, note, day, tx_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, goal_id, amount, note[:200], day.isoformat(), tx_id, now_iso()),
        )
        return True

    async def distribute(
        self, user_id: int, amount: int, day: date, tx_id: int | None = None
    ) -> list[tuple[int, int]]:
        """Раскладывает сумму по плану. Один и тот же доход второй раз не распределяем."""
        if tx_id is not None and await self.is_distributed(user_id, tx_id):
            return []
        goals = await self.list(user_id)
        shares = split_by_plan(amount, goals)
        stamp = now_iso()
        await self.db.executemany(
            "INSERT INTO goal_ops (user_id, goal_id, amount, note, day, tx_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(user_id, gid, share, "По плану", day.isoformat(), tx_id, stamp)
             for gid, share in shares],
        )
        return shares

    async def is_distributed(self, user_id: int, tx_id: int) -> bool:
        return bool(await self.db.scalar(
            "SELECT COUNT(*) FROM goal_ops WHERE user_id = ? AND tx_id = ?", (user_id, tx_id)
        ))

    async def history(self, user_id: int, goal_id: int, limit: int = 6):
        return await self.db.fetchall(
            "SELECT * FROM goal_ops WHERE user_id = ? AND goal_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, goal_id, limit),
        )

    async def total_saved(self, user_id: int) -> int:
        return await self.db.scalar(
            "SELECT SUM(amount) FROM goal_ops WHERE user_id = ?", (user_id,)
        )

    async def plan_percent(self, user_id: int) -> float:
        return await self.db.scalar(
            "SELECT SUM(percent) FROM goals WHERE user_id = ?", (user_id,)
        )
