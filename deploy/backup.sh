#!/usr/bin/env bash
#
# Резервная копия базы бота (все данные пользователей лежат в одном файле).
#
#     bash deploy/backup.sh
#
# Копия делается средствами самой SQLite, поэтому она целостная даже при работающем боте
# (простое cp в момент записи могло бы дать битый файл). Копии сжимаются, лежат в data/backups,
# а старше 30 дней удаляются.
#
# Раз в сутки по расписанию:
#     crontab -e
#     30 4 * * * /bin/bash /путь/к/боту/deploy/backup.sh >/dev/null 2>&1

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"   # иначе find жалуется, если запустили из чужой папки (например, из /root)
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
KEEP_DAYS="${KEEP_DAYS:-30}"

# Путь к базе: переменная окружения, затем .env, затем значение по умолчанию.
DB="${DB_PATH:-}"
if [ -z "$DB" ] && [ -f "$ROOT/.env" ]; then
    DB="$(grep -E '^DB_PATH=' "$ROOT/.env" | tail -n1 | cut -d= -f2- | tr -d '\r' || true)"
fi
DB="${DB:-data/finbot.db}"
case "$DB" in
    /*) ;;
    *) DB="$ROOT/$DB" ;;
esac

if [ ! -f "$DB" ]; then
    echo "Нет базы: $DB" >&2
    exit 1
fi

OUT_DIR="$ROOT/data/backups"
mkdir -p "$OUT_DIR"
chmod 700 "$OUT_DIR"

STAMP="$(date +%Y-%m-%d_%H%M%S)"
TARGET="$OUT_DIR/finbot-$STAMP.db"

"$PYTHON" - "$DB" "$TARGET" <<'PY'
import sqlite3
import sys

source = sqlite3.connect(sys.argv[1])
target = sqlite3.connect(sys.argv[2])
with target:
    source.backup(target)
target.close()
source.close()
PY

gzip -f "$TARGET"
chmod 600 "$TARGET.gz"
find "$OUT_DIR" -name 'finbot-*.db.gz' -mtime "+$KEEP_DAYS" -delete

echo "Копия: $TARGET.gz ($(du -h "$TARGET.gz" | cut -f1))"
