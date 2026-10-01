"""Схема SQLite.

Деньги хранятся целыми числами в минимальных единицах (копейки, центы):
так суммы не накапливают ошибку округления. Во всех таблицах есть user_id,
а при удалении пользователя всё его каскадно исчезает.
"""

SCHEMA_VERSION = 2

# Что добавить в базу, созданную прежней версией: (таблица, столбец, определение).
# Суммы там были в одной валюте, поэтому base_amount = amount, а валюта счёта = валюте пользователя.
MIGRATION_COLUMNS = [
    ("accounts", "currency", "TEXT NOT NULL DEFAULT 'RUB'"),
    ("transactions", "base_amount", "INTEGER NOT NULL DEFAULT 0"),
    ("transactions", "to_amount", "INTEGER"),
    ("users", "mascot", "INTEGER NOT NULL DEFAULT 1"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                 INTEGER PRIMARY KEY,
    first_name         TEXT    NOT NULL DEFAULT '',
    username           TEXT,
    currency           TEXT    NOT NULL DEFAULT 'RUB',
    tz                 TEXT    NOT NULL,
    default_account_id INTEGER,
    reminder_hour      INTEGER,
    digest             INTEGER NOT NULL DEFAULT 1,
    last_reminder_day  TEXT,
    last_digest_week   TEXT,
    last_debt_day      TEXT,
    mascot             INTEGER NOT NULL DEFAULT 1,
    created_at         TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name     TEXT    NOT NULL,
    emoji    TEXT    NOT NULL DEFAULT '💳',
    initial  INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    currency TEXT    NOT NULL DEFAULT 'RUB'
);
CREATE INDEX IF NOT EXISTS idx_accounts_user ON accounts(user_id);

-- Курсы к рублю (по данным ЦБ): сколько рублей стоит единица валюты
CREATE TABLE IF NOT EXISTS rates (
    code    TEXT PRIMARY KEY,
    rub     REAL NOT NULL,
    updated TEXT NOT NULL
);

-- Свой курс пользователя: сколько единиц ОСНОВНОЙ валюты стоит единица code
CREATE TABLE IF NOT EXISTS user_rates (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code    TEXT    NOT NULL,
    rate    REAL    NOT NULL CHECK (rate > 0),
    PRIMARY KEY (user_id, code)
);

CREATE TABLE IF NOT EXISTS categories (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind     TEXT    NOT NULL CHECK (kind IN ('income', 'expense')),
    name     TEXT    NOT NULL,
    emoji    TEXT    NOT NULL DEFAULT '📦',
    passive  INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    UNIQUE (user_id, kind, name)
);

CREATE TABLE IF NOT EXISTS sources (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name     TEXT    NOT NULL,
    emoji    TEXT    NOT NULL DEFAULT '🏷',
    archived INTEGER NOT NULL DEFAULT 0,
    UNIQUE (user_id, name)
);

CREATE TABLE IF NOT EXISTS transactions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind          TEXT    NOT NULL CHECK (kind IN ('income', 'expense', 'transfer')),
    amount        INTEGER NOT NULL CHECK (amount > 0),
    base_amount   INTEGER NOT NULL DEFAULT 0,
    to_amount     INTEGER,
    account_id    INTEGER NOT NULL REFERENCES accounts(id),
    to_account_id INTEGER REFERENCES accounts(id),
    category_id   INTEGER REFERENCES categories(id),
    source_id     INTEGER REFERENCES sources(id),
    note          TEXT    NOT NULL DEFAULT '',
    day           TEXT    NOT NULL,
    created_at    TEXT    NOT NULL,
    recurring_id  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tx_user_day ON transactions(user_id, day);
CREATE INDEX IF NOT EXISTS idx_tx_account ON transactions(account_id);

CREATE TABLE IF NOT EXISTS goals (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name     TEXT    NOT NULL,
    emoji    TEXT    NOT NULL DEFAULT '🎯',
    target   INTEGER,
    percent  REAL    NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_goals_user ON goals(user_id);

CREATE TABLE IF NOT EXISTS goal_ops (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    goal_id    INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    amount     INTEGER NOT NULL,
    note       TEXT    NOT NULL DEFAULT '',
    day        TEXT    NOT NULL,
    tx_id      INTEGER,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_goal_ops_goal ON goal_ops(goal_id);

CREATE TABLE IF NOT EXISTS budgets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category_id INTEGER REFERENCES categories(id),
    amount      INTEGER NOT NULL CHECK (amount > 0)
);
CREATE INDEX IF NOT EXISTS idx_budgets_user ON budgets(user_id);

CREATE TABLE IF NOT EXISTS recurring (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind        TEXT    NOT NULL CHECK (kind IN ('income', 'expense')),
    amount      INTEGER NOT NULL CHECK (amount > 0),
    category_id INTEGER REFERENCES categories(id),
    source_id   INTEGER REFERENCES sources(id),
    account_id  INTEGER NOT NULL REFERENCES accounts(id),
    note        TEXT    NOT NULL DEFAULT '',
    freq        TEXT    NOT NULL CHECK (freq IN ('daily', 'weekly', 'monthly')),
    param       INTEGER NOT NULL DEFAULT 0,
    next_day    TEXT    NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_recurring_user ON recurring(user_id);

CREATE TABLE IF NOT EXISTS debts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    direction  TEXT    NOT NULL CHECK (direction IN ('owe', 'owed')),
    person     TEXT    NOT NULL,
    amount     INTEGER NOT NULL CHECK (amount > 0),
    paid       INTEGER NOT NULL DEFAULT 0,
    due_day    TEXT,
    note       TEXT    NOT NULL DEFAULT '',
    closed     INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_debts_user ON debts(user_id);

CREATE TABLE IF NOT EXISTS articles (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT    NOT NULL,
    body       TEXT    NOT NULL DEFAULT '',
    origin     TEXT    NOT NULL DEFAULT 'own',
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_articles_user ON articles(user_id);
"""
