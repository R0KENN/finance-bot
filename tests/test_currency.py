"""Мультивалютность: курсы, пересчёт, обмен, смена основной валюты, миграция."""
import unittest
from datetime import date
from decimal import Decimal

from finbot.db import Database
from finbot.repos import Repos
from finbot.services.currency_switch import switch_base
from finbot.services.parser import parse_quick
from finbot.services.rates import RateUnknown
from tests.harness import TEST_RATES
from tests.test_flows import FlowCase


class ParserCurrencyTests(unittest.TestCase):
    today = date(2026, 10, 1)

    def test_currency_tokens(self):
        cases = {
            "+500$ зарплата": ("USD", 50000, "зарплата"), "кофе 250р": ("RUB", 25000, "кофе"),
            "20 евро обед": ("EUR", 2000, "обед"), "такси 300 usd": ("USD", 30000, "такси"),
            "+1,5к долларов фриланс": ("USD", 150000, "фриланс"), "кофе 250": (None, 25000, "кофе"),
            "сумка 500": (None, 50000, "сумка"), "хлеб 40 руб.": ("RUB", 4000, "хлеб"),
        }
        for text, (currency, amount, rest) in cases.items():
            entry = parse_quick(text, self.today)
            self.assertEqual((entry.currency, entry.amount, entry.text), (currency, amount, rest), text)


class RatesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        self.calls = 0

        async def fetch():
            self.calls += 1
            return {"RUB": 1.0, "USD": 91.5}

        self.repos = Repos(self.db, rate_fetcher=fetch)
        self.rates = self.repos.rates
        await self.rates.set_table(dict(TEST_RATES))
        await self.repos.ensure_user(1, "А", None)
        await self.repos.ensure_user(2, "Б", None)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_convert_and_cross(self):
        self.assertEqual(await self.rates.convert(1, 1000, "USD", "RUB"), 90000)
        self.assertEqual(await self.rates.convert(1, 90000, "RUB", "USD"), 1000)
        self.assertEqual(await self.rates.convert(1, 1000, "EUR", "USD"), 1111)       # 100/90
        self.assertEqual(await self.rates.convert(1, 777, "RUB", "RUB"), 777)
        self.assertEqual(await self.rates.convert(1, 1, "KZT", "RUB"), 0)             # 0,2 коп. округляются

    async def test_rounding_half_up(self):
        await self.rates.set_table({"USD": 0.5})
        self.assertEqual(await self.rates.convert(1, 3, "USD", "RUB"), 2)             # 1,5 -> 2

    async def test_unknown_rate(self):
        self.assertIsNone(await self.rates.rate(1, "AMD", "RUB"))
        with self.assertRaises(RateUnknown):
            await self.rates.convert(1, 100, "AMD", "RUB")
        self.assertFalse(await self.rates.has_rate(1, "AMD"))

    async def test_override_wins_and_is_per_user(self):
        await self.rates.set_override(1, "USD", Decimal("100"))
        self.assertEqual(await self.rates.convert(1, 100, "USD", "RUB"), 10000)
        self.assertEqual(await self.rates.convert(2, 100, "USD", "RUB"), 9000)        # у второго авто
        self.assertEqual(await self.rates.convert(1, 10000, "RUB", "USD"), 100)       # обратное направление
        await self.rates.set_override(1, "AMD", Decimal("0.23"))
        self.assertTrue(await self.rates.has_rate(1, "AMD"))
        self.assertFalse(await self.rates.has_rate(2, "AMD"))
        self.assertEqual(await self.rates.describe(1, "USD"), (Decimal("100"), "свой"))
        self.assertEqual((await self.rates.describe(2, "USD"))[1], "авто")
        await self.rates.clear_override(1, "USD")
        self.assertEqual(await self.rates.convert(1, 100, "USD", "RUB"), 9000)
        with self.assertRaises(ValueError):
            await self.rates.set_override(1, "USD", Decimal("0"))

    async def test_refresh_success_and_failure(self):
        self.assertTrue(await self.rates.refresh())
        self.assertEqual(await self.rates.convert(1, 100, "USD", "RUB"), 9150)
        self.assertIsNotNone(await self.rates.last_update())
        calls = self.calls
        await self.rates.refresh_if_stale()                  # свежие — сеть не трогаем
        self.assertEqual(self.calls, calls)

        async def broken():
            raise RuntimeError("сеть пропала")

        self.rates.fetcher = broken
        self.assertFalse(await self.rates.refresh())
        self.assertEqual(await self.rates.convert(1, 100, "USD", "RUB"), 9150)       # старые курсы целы

    async def test_unsupported_codes_ignored(self):
        await self.rates.set_table({"XXX": 5.0, "USD": 0.0})
        self.assertEqual(await self.db.scalar("SELECT COUNT(*) FROM rates WHERE code = 'XXX'"), 0)
        self.assertEqual(await self.rates.convert(1, 100, "USD", "RUB"), 9000)


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_database_gets_new_columns(self):
        import aiosqlite
        import tempfile
        import os
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        async with aiosqlite.connect(path) as conn:      # база от версии без валют
            await conn.executescript("""
                CREATE TABLE users (id INTEGER PRIMARY KEY, first_name TEXT NOT NULL DEFAULT '',
                    username TEXT, currency TEXT NOT NULL DEFAULT 'RUB', tz TEXT NOT NULL,
                    default_account_id INTEGER, reminder_hour INTEGER, digest INTEGER NOT NULL DEFAULT 1,
                    last_reminder_day TEXT, last_digest_week TEXT, created_at TEXT NOT NULL);
                CREATE TABLE accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                    name TEXT NOT NULL, emoji TEXT NOT NULL DEFAULT '', initial INTEGER NOT NULL DEFAULT 0,
                    archived INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                    kind TEXT NOT NULL, amount INTEGER NOT NULL, account_id INTEGER NOT NULL,
                    to_account_id INTEGER, category_id INTEGER, source_id INTEGER,
                    note TEXT NOT NULL DEFAULT '', day TEXT NOT NULL, created_at TEXT NOT NULL,
                    recurring_id INTEGER);
                INSERT INTO users (id, currency, tz, created_at) VALUES (5, 'EUR', 'UTC', 'x');
                INSERT INTO accounts (user_id, name) VALUES (5, 'Карта');
                INSERT INTO transactions (user_id, kind, amount, account_id, day, created_at)
                    VALUES (5, 'expense', 12345, 1, '2026-01-01', 'x');
            """)
            await conn.commit()
        db = Database(path)
        await db.connect()
        try:
            account = await db.fetchone("SELECT currency FROM accounts")
            tx = await db.fetchone("SELECT amount, base_amount, to_amount FROM transactions")
            self.assertEqual(account["currency"], "EUR")             # валюта счёта = валюте пользователя
            self.assertEqual((await db.fetchone("SELECT mascot FROM users"))["mascot"], 1)   # хомяк включён
            self.assertEqual((tx["amount"], tx["base_amount"], tx["to_amount"]), (12345, 12345, None))
        finally:
            await db.close()
        db = Database(path)                                          # повторное открытие ничего не ломает
        await db.connect()
        await db.close()


class MultiCurrencyFlowTests(FlowCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.onboard()
        self.repos = self.h.repos
        self.usd = await self.repos.accounts.add(1, "Доллары", "💵", 0, "USD")

    async def stats_text(self):
        await self.u.press_cb("go", "menu")
        await self.u.press("Статистика")
        return self.u.text

    async def test_quick_usd_income_uses_usd_account(self):
        await self.u.say("+500$ зарплата")
        self.assertIn("500", self.u.text)
        self.assertIn("$", self.u.text)
        row = (await self.repos.transactions.list(1, date(2000, 1, 1), date.today()))[0]
        self.assertEqual((row["account_id"], row["amount"], row["base_amount"], row["acc_currency"]),
                         (self.usd, 50000, 4_500_000, "USD"))
        self.assertIn("≈45 000", self.u.text)                  # пересчёт в рубли виден сразу

    async def test_usd_income_rub_expense_stats_in_base(self):
        await self.u.say("+500$ зарплата")
        await self.u.say("продукты 3000")
        text = await self.stats_text()
        self.assertIn("45 000", text)                          # доходы в рублях
        self.assertIn("3 000", text)
        self.assertIn("42 000", text)                          # итог
        accounts = {a["name"]: a for a in await self.repos.accounts.list(1)}
        self.assertEqual(accounts["Доллары"]["balance"], 50000)
        self.assertEqual(accounts["Карта"]["balance"], -300000)
        self.assertEqual(await self.repos.accounts.total(1), 4_500_000 - 300_000)

    async def test_history_keeps_rate_of_the_day(self):
        await self.u.say("+100$ фриланс")
        await self.repos.rates.set_table({"USD": 100.0})            # курс вырос
        await self.u.say("+100$ фриланс")
        rows = await self.repos.transactions.list(1, date(2000, 1, 1), date.today())
        self.assertEqual(sorted(r["base_amount"] for r in rows), [900_000, 1_000_000])
        income, _ = await self.repos.transactions.totals(1, date(2000, 1, 1), date.today())
        self.assertEqual(income, 1_900_000)                          # прошлая запись не «поплыла»

    async def test_card_switch_account_changes_currency(self):
        await self.u.press("➖ Расход")
        await self.u.say("20")
        await self.u.press("Рубль")
        await self.u.press("Прочее")
        self.assertIn("20 ₽", self.u.text)
        await self.u.press("Счёт")
        await self.u.press("Доллары")
        self.assertIn("20 $", self.u.text)
        self.assertIn("≈1 800", self.u.text)
        await self.u.press("Сохранить")
        row = (await self.repos.transactions.list(1, date(2000, 1, 1), date.today()))[0]
        self.assertEqual((row["amount"], row["base_amount"]), (2000, 180_000))

    async def test_currency_step_right_after_amount(self):
        await self.u.press_cb("go", "menu")
        await self.u.press("➖ Расход")
        await self.u.say("20")
        self.assertIn("В какой валюте 20", self.u.text)
        labels = self.u.screen.labels()
        self.assertEqual(labels[0].replace("✅ ", ""), "₽ Рубль")        # сначала валюты со счетами
        self.assertEqual(labels[1], "$ Доллар")
        await self.u.press("Доллар")
        self.assertIn("20 $", self.u.text)                           # уже на экране категории
        await self.u.press("Прочее")
        self.assertIn("Доллары", self.u.text)
        await self.u.press("Сохранить")
        row = (await self.repos.transactions.list(1, date(2000, 1, 1), date.today()))[0]
        self.assertEqual((row["acc_currency"], row["amount"], row["base_amount"]), ("USD", 2000, 180_000))

    async def test_currency_written_with_amount_skips_the_step(self):
        await self.u.press_cb("go", "menu")
        await self.u.press("➖ Расход")
        await self.u.say("20$")
        self.assertNotIn("В какой валюте", self.u.text)
        self.assertIn("категорию", self.u.text)
        self.assertIn("20 $", self.u.text)
        await self.u.press("Прочее")
        await self.u.press("Сохранить")
        row = (await self.repos.transactions.list(1, date(2000, 1, 1), date.today()))[0]
        self.assertEqual((row["acc_currency"], row["amount"]), ("USD", 2000))

    async def test_written_currency_without_rate_keeps_prompt(self):
        await self.u.press_cb("go", "menu")
        await self.u.press("➖ Расход")
        await self.u.say("500 драм")
        self.assertIn("Не знаю курс AMD", self.u.text)
        await self.u.say("20 евро")                                   # можно исправиться тут же
        self.assertIn("категорию", self.u.text)
        self.assertIn("20 €", self.u.text)

    async def test_step_new_currency_account_and_cancel(self):
        await self.u.press_cb("go", "menu")
        await self.u.press("➕ Доход")
        await self.u.say("1000")
        await self.u.press("Евро")                                    # счёта в евро нет — создаётся
        self.assertEqual(len([a for a in await self.repos.accounts.list(1) if a["currency"] == "EUR"]), 1)
        await self.u.press("Отмена")
        self.assertIn("Мои финансы", self.u.text)
        self.assertEqual(await self.h.db.scalar("SELECT COUNT(*) FROM transactions"), 0)

    async def test_bare_number_then_currency(self):
        await self.u.say("300")
        await self.u.press("Расход")
        self.assertIn("В какой валюте 300", self.u.text)
        await self.u.press("Доллар")
        await self.u.press("Прочее")
        await self.u.press("Сохранить")
        row = (await self.repos.transactions.list(1, date(2000, 1, 1), date.today()))[0]
        self.assertEqual((row["acc_currency"], row["amount"]), ("USD", 30000))

    async def test_currency_button_on_expense_card(self):
        await self.u.press_cb("go", "menu")
        await self.u.press("➖ Расход")
        await self.u.say("20")
        await self.u.press("Рубль")
        await self.u.press("Прочее")
        self.assertIn("20 ₽", self.u.text)
        await self.u.press("Валюта")
        self.assertIn("В какой валюте", self.u.text)
        await self.u.press("Доллар")
        self.assertIn("20 $", self.u.text)
        self.assertIn("Доллары", self.u.text)                         # счёт подставился сам
        self.assertIn("≈1 800", self.u.text)
        await self.u.press("Сохранить")
        row = (await self.repos.transactions.list(1, date(2000, 1, 1), date.today()))[0]
        self.assertEqual((row["acc_currency"], row["amount"], row["base_amount"]), ("USD", 2000, 180_000))

    async def test_currency_button_creates_account_and_switches_back(self):
        await self.u.press_cb("go", "menu")
        await self.u.press("➕ Доход")
        await self.u.say("1000")
        await self.u.press("Рубль")
        await self.u.press("Зарплата")
        await self.u.press("Без источника")
        await self.u.press("Валюта")
        await self.u.press("Евро")
        self.assertIn("1 000 €", self.u.text)
        self.assertEqual(len([a for a in await self.repos.accounts.list(1) if a["currency"] == "EUR"]), 1)
        await self.u.press("Валюта")
        await self.u.press("Рубль")
        self.assertIn("1 000 ₽", self.u.text)
        self.assertIn("Карта", self.u.text)                           # вернулись на счёт по умолчанию
        await self.u.press("Валюта")
        await self.u.press("Евро")                                    # счёт уже есть — второй не создаётся
        self.assertEqual(len([a for a in await self.repos.accounts.list(1) if a["currency"] == "EUR"]), 1)
        await self.u.press("Сохранить")
        row = (await self.repos.transactions.list(1, date(2000, 1, 1), date.today()))[0]
        self.assertEqual((row["acc_currency"], row["base_amount"]), ("EUR", 10_000_000))

    async def test_currency_button_without_rate_is_refused(self):
        await self.u.press_cb("go", "menu")
        await self.u.press("➖ Расход")
        await self.u.say("20")
        await self.u.press("Рубль")
        await self.u.press("Прочее")
        await self.u.press("Валюта")
        await self.u.press("Драм")
        self.assertTrue(any("Нет курса AMD" in a for a in self.h.session.alerts))
        self.assertIn("В какой валюте", self.u.text)                  # остались на выборе валюты

    async def test_amount_prompt_names_currency_when_several(self):
        await self.u.press_cb("go", "menu")
        await self.u.press("➖ Расход")
        self.assertIn("Валюту выберешь следующим шагом", self.u.text)

    async def test_budget_alert_uses_base_amount(self):
        cat = (await self.repos.categories.list(1, "expense"))[0]["id"]
        await self.repos.budgets.set_limit(1, cat, 100_000)         # 1 000 ₽
        await self.u.press("➖ Расход")
        await self.u.say("10")
        await self.u.press("Доллар")                                  # валюта — сразу после суммы
        await self.u.press((await self.repos.categories.list(1, "expense"))[0]["name"])
        await self.u.press("Сохранить")                              # 10 $ = 900 ₽ = 90% лимита
        self.assertIn("использовано 90%", self.u.text)

    async def test_exchange_transfer_with_suggested_and_manual_amount(self):
        await self.repos.accounts.set_initial(1, self.usd, 100_000)  # 1 000 $
        await self.u.press_cb("go", "accounts")
        self.assertIn("1 000 $", self.u.text)
        await self.u.press("Перевод")
        await self.u.press("Доллары")
        await self.u.press("Карта")
        await self.u.say("200")
        self.assertIn("Сколько пришло", self.u.text)
        self.assertIn("18 000", " ".join(self.u.screen.labels()))
        await self.u.press("по курсу")
        self.assertIn("200 $ → 18 000 ₽", self.u.text)
        # реальный обмен прошёл по другому курсу
        await self.u.press("Перевод")
        await self.u.press("Доллары")
        await self.u.press("Карта")
        await self.u.say("100")
        await self.u.say("9300")
        accounts = {a["name"]: a["balance"] for a in await self.repos.accounts.list(1)}
        self.assertEqual(accounts["Доллары"], 100_000 - 20_000 - 10_000)
        self.assertEqual(accounts["Карта"], 1_800_000 + 930_000)
        income, expense = await self.repos.transactions.totals(1, date(2000, 1, 1), date.today())
        self.assertEqual((income, expense), (0, 0))                  # обмен — не доход и не расход
        await self.u.press_cb("go", "hist")
        self.assertIn("100 $ → 9 300 ₽", self.u.text)

    async def test_edit_amount_recalculates_base(self):
        await self.u.say("+100$ фриланс")
        tx = (await self.repos.transactions.list(1, date(2000, 1, 1), date.today()))[0]
        await self.u.press_cb("tx", tx["id"])
        await self.u.press("Сумма")
        await self.u.say("200")
        row = await self.repos.transactions.get(1, tx["id"])
        self.assertEqual((row["amount"], row["base_amount"]), (20000, 1_800_000))

    async def test_new_account_in_currency_with_known_rate(self):
        await self.u.press_cb("go", "accounts")
        await self.u.press("Новый счёт")
        await self.u.say("🇪🇺 Евро-счёт")
        await self.u.press("Евро")
        self.assertIn("Сколько на счёте сейчас", self.u.text)
        await self.u.say("500")
        accounts = {a["name"]: a for a in await self.repos.accounts.list(1)}
        self.assertEqual((accounts["Евро-счёт"]["currency"], accounts["Евро-счёт"]["initial"]),
                         ("EUR", 50000))
        self.assertIn("500 €", self.u.text)
        self.assertIn("≈50 000", self.u.text)

    async def test_new_account_with_unknown_rate_asks_for_it(self):
        await self.u.press_cb("go", "accounts")
        await self.u.press("Новый счёт")
        await self.u.say("Армения")
        await self.u.press("Драм")
        self.assertIn("Не знаю курс AMD", self.u.text)
        await self.u.say("не число")
        self.assertIn("положительное число", self.u.text)
        await self.u.say("0,23")
        self.assertIn("Сколько на счёте сейчас", self.u.text)
        await self.u.say("10000")
        accounts = {a["name"]: a for a in await self.repos.accounts.list(1)}
        self.assertEqual(accounts["Армения"]["currency"], "AMD")
        self.assertEqual(await self.repos.rates.convert(1, 10000, "AMD", "RUB"), 2300)

    async def test_quick_entry_in_currency_without_rate_is_refused(self):
        await self.u.say("кофе 500 драм")
        self.assertIn("Не знаю курс для AMD", self.u.text)
        self.assertEqual(await self.h.db.scalar("SELECT COUNT(*) FROM transactions"), 0)
        await self.u.say("кофе 5 евро")                              # курс известен: счёт создаётся сам
        self.assertIn("Создал счёт", self.u.text)
        self.assertEqual(len([a for a in await self.repos.accounts.list(1) if a["currency"] == "EUR"]), 1)

    async def test_currencies_screen_and_manual_rate(self):
        await self.u.press_cb("go", "currencies")
        self.assertIn("1 $ = 90,00 ₽", self.u.text)
        self.assertIn("авто", self.u.text)
        await self.u.press("Курс USD")
        await self.u.say("95,5")
        self.assertIn("1 $ = 95,50 ₽", self.u.text)
        self.assertIn("свой", self.u.text)
        await self.u.say("+100$ фриланс")
        row = (await self.repos.transactions.list(1, date(2000, 1, 1), date.today()))[0]
        self.assertEqual(row["base_amount"], 955_000)
        await self.u.press_cb("go", "currencies")
        await self.u.press("Авто USD")
        self.assertIn("авто", self.u.text)

    async def test_refresh_button(self):
        await self.u.press_cb("go", "currencies")
        await self.u.press("Обновить курсы")
        self.assertTrue(any("обновлены" in s.text.lower() for s in self.h.session.screens.values()))

    async def test_switch_base_currency_converts_everything(self):
        await self.u.say("+500$ зарплата")
        await self.u.say("продукты 4500")
        cat = (await self.repos.categories.list(1, "expense"))[0]["id"]
        await self.repos.budgets.set_limit(1, cat, 900_000)          # 9 000 ₽
        goal = await self.repos.goals.add(1, "Мечта", "✨", 9_000_000, 10)
        await self.repos.goals.add_op(1, goal, 1_800_000, date.today())
        await self.repos.debts.add(1, "owed", "Иван", 180_000)
        await self.repos.rates.set_override(1, "KZT", Decimal("0.25"))

        await self.u.press_cb("go", "currencies")
        await self.u.press("Сменить основную")
        await self.u.press("Доллар")
        self.assertIn("пересчитаются", self.u.text)
        await self.u.press("Да, сменить")
        self.assertIn("$", self.u.text)

        profile = await self.repos.users.get(1)
        self.assertEqual(profile.currency, "USD")
        income, expense = await self.repos.transactions.totals(1, date(2000, 1, 1), date.today())
        self.assertEqual((income, expense), (50000, 5000))           # 500 $ и 4 500 ₽ = 50 $
        self.assertEqual((await self.repos.goals.get(1, goal))["target"], 100_000)
        self.assertEqual((await self.repos.goals.get(1, goal))["saved"], 20_000)
        self.assertEqual((await self.repos.budgets.list(1))[0]["amount"], 10_000)
        self.assertEqual((await self.repos.debts.list(1))[0]["amount"], 2000)
        # свой курс пересчитан в новую основную: 1 тенге = 0,25 ₽ = 0,25/90 $
        self.assertEqual(await self.repos.rates.convert(1, 36000, "KZT", "USD"), 100)
        # счета остались в своих валютах
        currencies = sorted(a["currency"] for a in await self.repos.accounts.list(1))
        self.assertEqual(currencies, ["RUB", "RUB", "USD"])
        # статистика теперь в долларах
        text = await self.stats_text()
        self.assertIn("500 $", text)

    async def test_switch_base_without_rate_changes_nothing(self):
        await self.u.say("кофе 250")
        profile = await self.repos.users.get(1)
        with self.assertRaises(RateUnknown):
            await switch_base(self.repos, profile, "AMD")
        self.assertEqual((await self.repos.users.get(1)).currency, "RUB")
        income, expense = await self.repos.transactions.totals(1, date(2000, 1, 1), date.today())
        self.assertEqual(expense, 25000)

    async def test_distribution_uses_base_amount(self):
        await self.repos.goals.add(1, "Подушка", "🛡", None, 10)
        await self.u.say("+500$ зарплата")
        await self.u.press("Распределить по плану")
        self.assertIn("4 500", self.u.text)                     # 10% от 45 000 ₽
        await self.u.press("✅ Распределить")
        self.assertEqual(await self.repos.goals.total_saved(1), 450_000)

    async def test_export_has_currency_columns(self):
        await self.u.say("+500$ зарплата")
        await self.u.press_cb("go", "settings")
        await self.u.press("Экспорт")
        from aiogram.methods import SendDocument
        data = self.u.sent(SendDocument)[0].document.data.decode("utf-8-sig")
        self.assertIn("Сумма в основной валюте", data)
        self.assertIn("500,00;USD;45000,00;RUB", data)

    async def test_other_user_not_affected_by_my_rates(self):
        bob = self.h.client(2, "Боб")
        await self.onboard(bob)
        await self.repos.rates.set_override(1, "USD", Decimal("1000"))
        self.assertEqual(await self.repos.rates.convert(2, 100, "USD", "RUB"), 9000)
        self.assertEqual(await self.repos.rates.overrides(2), {})

    async def test_onboarding_currency_sets_account_currency(self):
        eve = self.h.client(3, "Ева")
        await eve.say("/start")
        await eve.press("Доллар")
        accounts = await self.repos.accounts.list(3)
        self.assertEqual({a["currency"] for a in accounts}, {"USD"})
        await eve.say("кофе 5")
        row = (await self.repos.transactions.list(3, date(2000, 1, 1), date.today()))[0]
        self.assertEqual((row["amount"], row["base_amount"], row["acc_currency"]), (500, 500, "USD"))

    async def test_recurring_in_usd_account(self):
        from finbot import scheduler
        await self.repos.recurring.add(1, "income", 10000, self.usd, "daily", 0, date.today(),
                                       note="Подписка клиента")
        created = await scheduler.apply_recurring(self.repos, await self.repos.users.get(1))
        self.assertEqual(len(created), 1)
        self.assertIn("$", created[0])
        row = (await self.repos.transactions.list(1, date(2000, 1, 1), date.today()))[0]
        self.assertEqual((row["amount"], row["base_amount"]), (10000, 900_000))

    async def test_wipe_removes_user_rates(self):
        await self.repos.rates.set_override(1, "USD", Decimal("95"))
        await self.repos.users.delete(1)
        self.assertEqual(await self.h.db.scalar("SELECT COUNT(*) FROM user_rates WHERE user_id = 1"), 0)


if __name__ == "__main__":
    unittest.main()
