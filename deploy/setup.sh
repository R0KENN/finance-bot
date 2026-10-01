#!/usr/bin/env bash
#
# Установка финансового бота как службы systemd.
#
# Запускать НА СЕРВЕРЕ из папки бота:
#     bash deploy/setup.sh
#
# Скрипт можно запускать повторно (после обновления кода): .env и база не трогаются,
# зависимости доставляются, служба перезапускается.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="${SUDO_USER:-$(id -un)}"
SERVICE="finbot"

echo "Папка бота: $ROOT"
echo "Служба будет работать от пользователя: $USER_NAME"
echo

# --- Python ------------------------------------------------------------
if ! command -v python3 >/dev/null; then
    echo "Нет python3. Установите: sudo apt install -y python3 python3-venv" >&2
    exit 1
fi

if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"; then
    echo "Нужен Python 3.10 или новее, а здесь: $(python3 -V)" >&2
    exit 1
fi

# python3-venv в Debian и Ubuntu идёт отдельным пакетом: без него venv создаётся без pip.
if ! python3 -c "import venv, ensurepip" 2>/dev/null; then
    echo "Нет модуля venv. Установите: sudo apt install -y python3-venv" >&2
    exit 1
fi

# --- Настройки ---------------------------------------------------------
if [ ! -f "$ROOT/.env" ]; then
    cp "$ROOT/.env.example" "$ROOT/.env"
    chmod 600 "$ROOT/.env"
    echo "Создан файл .env. Впишите в него BOT_TOKEN (токен от @BotFather)" >&2
    echo "и, желательно, ALLOWED_USERS (ваш Telegram id), затем запустите скрипт снова:" >&2
    echo "    nano $ROOT/.env" >&2
    exit 1
fi

if ! grep -q "^BOT_TOKEN=." "$ROOT/.env"; then
    echo "В $ROOT/.env не задан BOT_TOKEN." >&2
    exit 1
fi

# Токен и ключи не должны читаться другими пользователями сервера.
chmod 600 "$ROOT/.env"

if ! grep -q "^ALLOWED_USERS=." "$ROOT/.env"; then
    echo "ВНИМАНИЕ: ALLOWED_USERS пуст — ботом сможет пользоваться любой, кто его найдёт." >&2
    echo "Для личных финансов лучше вписать свой Telegram id (его покажет @userinfobot)." >&2
    echo
fi

# --- Окружение и зависимости -------------------------------------------
if [ ! -x "$ROOT/.venv/bin/python" ]; then
    echo "Создаю окружение..."
    python3 -m venv "$ROOT/.venv"
fi

"$ROOT/.venv/bin/pip" install --quiet --upgrade pip
echo "Ставлю зависимости (первый раз это несколько минут: matplotlib тяжёлый)..."
"$ROOT/.venv/bin/pip" install --quiet -r "$ROOT/requirements.txt"
echo "Зависимости готовы."
mkdir -p "$ROOT/data"

# --- Проверка до запуска -----------------------------------------------
# Ошибка импорта или сломанная база лучше видны здесь, чем в журнале упавшей службы.
echo "Проверяю код и базу..."
(
    cd "$ROOT"
    "$ROOT/.venv/bin/python" - <<'PY'
import asyncio

from finbot import config
from finbot.db import Database
from finbot.handlers import build_router


async def check() -> None:
    db = Database(config.DB_PATH)
    await db.connect()          # создаёт базу или обновляет старую до текущей схемы
    await db.close()
    build_router()


asyncio.run(check())
print("  база:", config.DB_PATH)
print("  доступ только для:", sorted(config.ALLOWED_USERS) or "всех")
PY
)

# --- Служба ------------------------------------------------------------
echo "Устанавливаю службу systemd..."
sed -e "s|__ROOT__|$ROOT|g" -e "s|__USER__|$USER_NAME|g" \
    "$ROOT/deploy/$SERVICE.service" \
    | sudo tee "/etc/systemd/system/$SERVICE.service" >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE" >/dev/null
sudo systemctl restart "$SERVICE"

sleep 5
echo
systemctl --no-pager --lines=0 status "$SERVICE" || true

cat <<'HINT'

Готово. Напишите боту /start в Telegram.

Живой журнал:   journalctl -u finbot -f
Состояние:      systemctl status finbot
Перезапуск:     sudo systemctl restart finbot
Остановка:      sudo systemctl stop finbot

Резервная копия базы:  bash deploy/backup.sh
После перезагрузки сервера бот поднимется сам.
HINT
