from __future__ import annotations

from datetime import datetime, timezone

from ..db import Database


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Repo:
    def __init__(self, db: Database):
        self.db = db

    async def owns(self, table: str, user_id: int, row_id: int | None) -> bool:
        """Принадлежит ли запись пользователю. None считаем допустимым (поле не задано)."""
        if row_id is None:
            return True
        found = await self.db.fetchone(
            f"SELECT 1 FROM {table} WHERE id = ? AND user_id = ?", (row_id, user_id)
        )
        return found is not None
