from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..config import DEFAULT_TZ
from ..services.dates import today_in
from ..services.money import symbol_of
from .base import Repo, now_iso


def load_zone(name: str) -> tzinfo:
    """Часовой пояс по имени. Если базы поясов нет (Windows без tzdata) — UTC, а не падение."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


@dataclass
class Profile:
    id: int
    first_name: str
    currency: str
    tz_name: str
    default_account_id: int | None
    reminder_hour: int | None
    digest: bool
    last_reminder_day: str | None
    last_digest_week: str | None
    last_debt_day: str | None = None
    mascot: bool = True

    @property
    def symbol(self) -> str:
        return symbol_of(self.currency)

    @property
    def tz(self) -> tzinfo:
        return load_zone(self.tz_name)

    @property
    def today(self) -> date:
        return today_in(self.tz)


_EDITABLE = {
    "first_name", "username", "currency", "tz", "default_account_id", "reminder_hour",
    "digest", "last_reminder_day", "last_digest_week", "last_debt_day",
    "mascot",
}


def _profile(row) -> Profile:
    return Profile(
        id=row["id"],
        first_name=row["first_name"],
        currency=row["currency"],
        tz_name=row["tz"],
        default_account_id=row["default_account_id"],
        reminder_hour=row["reminder_hour"],
        digest=bool(row["digest"]),
        last_reminder_day=row["last_reminder_day"],
        last_digest_week=row["last_digest_week"],
        last_debt_day=row["last_debt_day"],
        mascot=bool(row["mascot"]),
    )


class UsersRepo(Repo):
    async def get(self, user_id: int) -> Profile | None:
        row = await self.db.fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
        return _profile(row) if row else None

    async def create(self, user_id: int, first_name: str, username: str | None) -> Profile:
        await self.db.execute(
            "INSERT INTO users (id, first_name, username, tz, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, first_name or "", username, DEFAULT_TZ, now_iso()),
        )
        return await self.get(user_id)

    async def update(self, user_id: int, **fields) -> None:
        bad = set(fields) - _EDITABLE
        if bad or not fields:
            raise ValueError(f"Нельзя менять поля: {sorted(bad)}")
        columns = ", ".join(f"{name} = ?" for name in fields)
        await self.db.execute(
            f"UPDATE users SET {columns} WHERE id = ?", (*fields.values(), user_id)
        )

    async def delete(self, user_id: int) -> None:
        """Стирает пользователя и каскадом все его данные."""
        await self.db.execute("DELETE FROM users WHERE id = ?", (user_id,))

    async def for_scheduler(self) -> list[Profile]:
        rows = await self.db.fetchall("SELECT * FROM users")
        return [_profile(row) for row in rows]
