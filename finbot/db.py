"""Тонкая обёртка над aiosqlite: одно соединение на процесс."""
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import aiosqlite

from .schema import MIGRATION_COLUMNS, SCHEMA, SCHEMA_VERSION


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            await self._conn.execute("PRAGMA journal_mode = WAL")
            await self._conn.execute("PRAGMA synchronous = NORMAL")
        # LOWER() в SQLite понимает только латиницу — для кириллицы нужна своя функция
        await self._conn.create_function(
            "pylower", 1, lambda value: value.lower() if isinstance(value, str) else value,
            deterministic=True,
        )
        await self._conn.executescript(SCHEMA)
        await self._migrate()
        await self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        await self._conn.commit()

    async def _migrate(self) -> None:
        """Дописывает столбцы, которых нет в базе от прежней версии бота."""
        added = set()
        for table, column, definition in MIGRATION_COLUMNS:
            async with self._conn.execute(f"PRAGMA table_info({table})") as cur:
                names = {row["name"] for row in await cur.fetchall()}
            if column not in names:
                await self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                added.add((table, column))
        if ("accounts", "currency") in added:
            await self._conn.execute(
                "UPDATE accounts SET currency = "
                "(SELECT currency FROM users WHERE users.id = accounts.user_id)"
            )
        if ("transactions", "base_amount") in added:
            await self._conn.execute("UPDATE transactions SET base_amount = amount")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("База не подключена: вызови connect()")
        return self._conn

    async def fetchone(self, sql: str, params: Sequence[Any] = ()):
        async with self.conn.execute(sql, params) as cur:
            return await cur.fetchone()

    async def fetchall(self, sql: str, params: Sequence[Any] = ()):
        async with self.conn.execute(sql, params) as cur:
            return await cur.fetchall()

    async def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = 0):
        row = await self.fetchone(sql, params)
        return default if row is None or row[0] is None else row[0]

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Выполняет запись и сразу коммитит. Возвращает lastrowid."""
        async with self.conn.execute(sql, params) as cur:
            last = cur.lastrowid
        await self.conn.commit()
        return last or 0

    async def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        await self.conn.executemany(sql, list(rows))
        await self.conn.commit()
