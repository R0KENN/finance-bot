"""Настройки из переменных окружения (.env)."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _ids(raw: str) -> set[int]:
    parts = raw.replace(";", ",").replace(" ", ",").split(",")
    return {int(p) for p in parts if p.strip().lstrip("-").isdigit()}


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_PATH = Path(os.getenv("DB_PATH", "data/finbot.db"))
DEFAULT_TZ = os.getenv("TIMEZONE", "Europe/Moscow").strip()

# Пусто — бот открыт для всех. Иначе только перечисленные Telegram id.
ALLOWED_USERS = _ids(os.getenv("ALLOWED_USERS", ""))

# off — не пробовать «Статьи» (Rich Messages), всегда слать обычным текстом.
RICH_MESSAGES = os.getenv("RICH_MESSAGES", "auto").strip().lower() != "off"

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()
