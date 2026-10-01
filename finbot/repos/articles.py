from __future__ import annotations

from .base import Repo, now_iso


class ArticlesRepo(Repo):
    async def add(self, user_id: int, title: str, body: str, origin: str = "own") -> int:
        stamp = now_iso()
        return await self.db.execute(
            "INSERT INTO articles (user_id, title, body, origin, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, title.strip()[:120] or "Без названия", body, origin, stamp, stamp),
        )

    async def get(self, user_id: int, article_id: int):
        return await self.db.fetchone(
            "SELECT * FROM articles WHERE id = ? AND user_id = ?", (article_id, user_id)
        )

    async def list(self, user_id: int, limit: int = 6, offset: int = 0):
        return await self.db.fetchall(
            "SELECT * FROM articles WHERE user_id = ? ORDER BY updated_at DESC, id DESC "
            "LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        )

    async def count(self, user_id: int) -> int:
        return await self.db.scalar("SELECT COUNT(*) FROM articles WHERE user_id = ?", (user_id,))

    async def update(self, user_id: int, article_id: int, **fields) -> None:
        allowed = {"title", "body"}
        if not fields or set(fields) - allowed:
            raise ValueError("Нельзя менять эти поля")
        if "title" in fields:
            fields["title"] = fields["title"].strip()[:120] or "Без названия"
        columns = ", ".join(f"{name} = ?" for name in fields)
        await self.db.execute(
            f"UPDATE articles SET {columns}, updated_at = ? WHERE id = ? AND user_id = ?",
            (*fields.values(), now_iso(), article_id, user_id),
        )

    async def delete(self, user_id: int, article_id: int) -> None:
        await self.db.execute(
            "DELETE FROM articles WHERE id = ? AND user_id = ?", (article_id, user_id)
        )
