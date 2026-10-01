"""Резервная копия базы в личку админу: на диске сервера копий не остаётся."""
from __future__ import annotations

import asyncio
import gzip
import logging
import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import BufferedInputFile

from .. import config

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 49 * 1024 * 1024   # бот может отправить файл до 50 МБ
RETRY_AFTER = 600                   # после неудачной отправки ждём 10 минут

_retry_at = 0.0


def _snapshot(db_path: str) -> bytes:
    """Целостная сжатая копия базы, пока бот работает. Временный файл удаляется сразу."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "backup.db"
        source = sqlite3.connect(db_path, timeout=30)
        copy = sqlite3.connect(target)
        try:
            source.backup(copy)
            verdict = copy.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            copy.close()
            source.close()
        if verdict != "ok":
            raise RuntimeError(f"копия не прошла проверку: {verdict}")
        return gzip.compress(target.read_bytes(), compresslevel=6)


def _size(data: bytes) -> str:
    return f"{len(data) / 1024:.0f} КБ" if len(data) < 1024 * 1024 else f"{len(data) / 1024 / 1024:.1f} МБ"


async def send_backup(bot: Bot, admin_id: int, db_path: Path | str, stamp: str) -> None:
    """Шлёт копию админу. Слишком большую не шлёт, а сообщает об этом."""
    data = await asyncio.to_thread(_snapshot, str(db_path))
    if len(data) > TELEGRAM_LIMIT:
        await bot.send_message(
            admin_id,
            f"⚠️ Копия базы {stamp} весит {_size(data)} — больше лимита Telegram (50 МБ). "
            "Скопируй data/finbot.db с сервера вручную.")
        return
    await bot.send_document(
        admin_id,
        BufferedInputFile(data, filename=f"finbot-{stamp}.db.gz"),
        caption=f"💾 Копия базы · {stamp} · {_size(data)}\nРаспаковать: gunzip, получится finbot.db")


def today_stamp() -> str:
    return datetime.now(ZoneInfo(config.DEFAULT_TZ)).date().isoformat()


def _marker(db_path: Path | str) -> Path:
    return Path(str(db_path) + ".backup-day")


async def tick(bot: Bot, *, admin_id: int | None = None, db_path: Path | str | None = None,
               hour: int | None = None, now: datetime | None = None) -> bool:
    """Раз в сутки после BACKUP_HOUR шлёт копию. Возвращает True, если отправил.

    День отправки хранится в файле рядом с базой: перезапуск бота не вызывает повтор,
    а если сервер лежал в нужный час, копия уйдёт при первой возможности.
    """
    global _retry_at
    admin_id = config.ADMIN_ID if admin_id is None else admin_id
    if not admin_id:
        return False
    db_path = config.DB_PATH if db_path is None else db_path
    hour = config.BACKUP_HOUR if hour is None else hour
    now = now or datetime.now(ZoneInfo(config.DEFAULT_TZ))
    if now.hour < hour:
        return False

    today = now.date().isoformat()
    marker = _marker(db_path)
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == today:
        return False
    if time.monotonic() < _retry_at:
        return False

    try:
        await send_backup(bot, admin_id, db_path, today)
    except Exception:  # noqa: BLE001 — сбой копии не должен останавливать планировщик
        log.exception("Не удалось отправить копию базы админу (повтор через %d мин). "
                      "Админ должен хотя бы раз написать боту /start", RETRY_AFTER // 60)
        _retry_at = time.monotonic() + RETRY_AFTER
        return False
    marker.write_text(today, encoding="utf-8")
    log.info("Копия базы отправлена админу")
    return True
