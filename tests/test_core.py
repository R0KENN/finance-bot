import unittest
from datetime import date

from finbot.db import Database
from finbot.repos import Repos
from finbot.repos.goals import split_by_plan
from finbot.services import dates, money
from finbot.services.parser import match_category, parse_quick


class MoneyTests(unittest.TestCase):
    def test_parse_amount(self):
        cases = {
            "1500": 150000, "1 500": 150000, "1 500": 150000, "1500,50": 150050,
            "1500.5": 150050, "2к": 200000, "2 к": 200000, "1.5к": 150000, "1,5 млн": 150000000,
            "300 руб": 30000, "300р": 30000, "300 ₽": 30000,
        }
        for text, expected in cases.items():
            self.assertEqual(money.parse_amount(text), expected, text)

    def test_parse_amount_rejects_garbage(self):
        for text in ["", "abc", "0", "-5", "12abc", "1.234", "99999999999999999", "1,,5"]:
            self.assertIsNone(money.parse_amount(text), text)

    def test_fmt(self):
        nb = money.NBSP
        self.assertEqual(money.fmt(123456789, "₽"), f"1{nb}234{nb}567,89{nb}₽")
        self.assertEqual(money.fmt(100000, "₽"), f"1{nb}000{nb}₽")
        self.assertEqual(money.fmt(-5000, "$"), f"−50{nb}$")
        self.assertEqual(money.fmt(5000, "$", sign=True), f"+50{nb}$")
        self.assertEqual(money.fmt(0, "₽"), f"0{nb}₽")

    def test_bar_and_short(self):
        self.assertEqual(money.bar(0.5, 10), "▰▰▰▰▰▱▱▱▱▱")
        self.assertEqual(money.bar(7, 4), "▰▰▰▰")
        self.assertEqual(money.bar(float("nan"), 4), "▱▱▱▱")
        self.assertEqual(money.fmt_short(150_000_000), "1,5 млн")
        self.assertEqual(money.fmt_short(4_500_000), "45 тыс")
        self.assertEqual(money.fmt_short(250_000), "2,5 тыс")


class DatesTests(unittest.TestCase):
    today = date(2026, 10, 1)

    def test_resolve(self):
        self.assertEqual(dates.resolve("m", self.today)[:2], (date(2026, 10, 1), date(2026, 10, 31)))
        self.assertEqual(dates.resolve("2026-02", self.today)[:2], (date(2026, 2, 1), date(2026, 2, 28)))
        start, end, _ = dates.resolve("w", self.today)
        self.assertEqual((start.weekday(), (end - start).days), (0, 6))
        self.assertEqual(dates.resolve("y", self.today)[:2], (date(2026, 1, 1), date(2026, 12, 31)))
        self.assertEqual(dates.resolve("мусор", self.today)[0], date(2026, 10, 1))

    def test_shift_month(self):
        self.assertEqual(dates.shift_month_key("m", -1, self.today), "2026-09")
        self.assertEqual(dates.shift_month_key("2026-01", -1, self.today), "2025-12")
        self.assertEqual(dates.shift_month_key("2026-12", 1, self.today), "2027-01")

    def test_parse_user_date(self):
        t = self.today
        self.assertEqual(dates.parse_user_date("вчера", t), date(2026, 9, 30))
        self.assertEqual(dates.parse_user_date("15.09", t), date(2026, 9, 15))
        self.assertEqual(dates.parse_user_date("15.09.25", t), date(2025, 9, 15))
        self.assertEqual(dates.parse_user_date("2026-09-15", t), date(2026, 9, 15))
        self.assertEqual(dates.parse_user_date("30.12", date(2026, 1, 5)), date(2025, 12, 30))
        self.assertIsNone(dates.parse_user_date("31.02", t))
        self.assertIsNone(dates.parse_user_date("05.10.2026", t))   # будущее
        self.assertIsNone(dates.parse_user_date("привет", t))

    def test_next_occurrence(self):
        self.assertEqual(dates.next_occurrence("daily", 0, date(2026, 10, 1)), date(2026, 10, 2))
        # Среда = 2; 1 октября 2026 — четверг, следующая среда 7-го
        self.assertEqual(dates.next_occurrence("weekly", 2, date(2026, 10, 1)), date(2026, 10, 7))
        self.assertEqual(dates.next_occurrence("monthly", 31, date(2026, 1, 31)), date(2026, 2, 28))
        self.assertEqual(dates.next_occurrence("monthly", 15, date(2026, 12, 15)), date(2027, 1, 15))
        self.assertEqual(dates.first_occurrence("monthly", 1, date(2026, 10, 1)), date(2026, 10, 1))
        self.assertEqual(dates.first_occurrence("weekly", 3, date(2026, 10, 1)), date(2026, 10, 1))
        self.assertEqual(dates.first_occurrence("monthly", 15, date(2026, 10, 20)), date(2026, 11, 15))


class ParserTests(unittest.TestCase):
    today = date(2026, 10, 1)

    def test_quick(self):
        entry = parse_quick("кофе 250", self.today)
        self.assertEqual((entry.kind, entry.amount, entry.text), ("expense", 25000, "кофе"))
        entry = parse_quick("+50 000 зарплата", self.today)
        self.assertEqual((entry.kind, entry.amount, entry.text), ("income", 5000000, "зарплата"))
        entry = parse_quick("такси 1,5к вчера", self.today)
        self.assertEqual((entry.amount, entry.day, entry.text), (150000, date(2026, 9, 30), "такси"))
        entry = parse_quick("500", self.today)
        self.assertEqual((entry.amount, entry.text), (50000, ""))
        entry = parse_quick("продукты 1250 руб 15.09", self.today)
        self.assertEqual((entry.amount, entry.day, entry.text), (125000, date(2026, 9, 15), "продукты"))

    def test_quick_none(self):
        for text in ["привет", "", "купил хлеб", "x" * 300]:
            self.assertIsNone(parse_quick(text, self.today), text)

    def test_match_category(self):
        cats = [{"name": "Продукты"}, {"name": "Кафе и рестораны"}, {"name": "Транспорт"}]
        self.assertEqual(match_category("кофе", cats, "expense")["name"], "Кафе и рестораны")
        self.assertEqual(match_category("такси домой", cats, "expense")["name"], "Транспорт")
        self.assertEqual(match_category("продуктов набрал", cats, "expense")["name"], "Продукты")
        self.assertIsNone(match_category("хрень", cats, "expense"))


class PlanTests(unittest.TestCase):
    def test_split(self):
        goals = [{"id": 1, "percent": 10}, {"id": 2, "percent": 33.3}, {"id": 3, "percent": 0}]
        shares = dict(split_by_plan(100_000, goals))
        self.assertEqual(shares, {1: 10_000, 2: 33_300})
        self.assertLessEqual(sum(shares.values()), 100_000)


class RepoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        self.repos = Repos(self.db)
        self.alice = await self.repos.ensure_user(1, "Алиса", "alice")
        self.bob = await self.repos.ensure_user(2, "Боб", None)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_concurrent_first_contact(self):
        import asyncio
        profiles = await asyncio.gather(*[self.repos.ensure_user(7, "Гонка", None) for _ in range(5)])
        self.assertEqual({p.id for p in profiles}, {7})
        self.assertEqual(len(await self.repos.categories.list(7, "expense")), 16)   # без дублей
        self.assertEqual(len(await self.repos.accounts.list(7)), 2)

    async def test_seed(self):
        self.assertEqual(len(await self.repos.categories.list(1, "expense")), 16)
        self.assertEqual(len(await self.repos.categories.list(1, "income")), 10)
        self.assertEqual(len(await self.repos.accounts.list(1)), 2)
        again = await self.repos.ensure_user(1, "Алиса", "alice")
        self.assertEqual(again.id, 1)
        self.assertEqual(len(await self.repos.categories.list(1, "expense")), 16)

    async def _acc(self, uid):
        return (await self.repos.default_account(await self.repos.users.get(uid)))["id"]

    async def test_isolation(self):
        tx = self.repos.transactions
        a_acc, b_acc = await self._acc(1), await self._acc(2)
        a_cat = (await self.repos.categories.list(1, "expense"))[0]["id"]
        tx_id = await tx.add(1, "expense", 10000, a_acc, date(2026, 10, 1), category_id=a_cat)

        self.assertIsNone(await tx.get(2, tx_id))
        self.assertFalse(await tx.delete(2, tx_id))
        self.assertEqual(await tx.totals(2, date(2026, 1, 1), date(2026, 12, 31)), (0, 0))
        self.assertEqual(await tx.list(2, date(2026, 1, 1), date(2026, 12, 31)), [])
        with self.assertRaises(PermissionError):
            await tx.add(2, "expense", 100, a_acc, date(2026, 10, 1))      # чужой счёт
        with self.assertRaises(PermissionError):
            await tx.add(2, "expense", 100, b_acc, date(2026, 10, 1), category_id=a_cat)
        with self.assertRaises(PermissionError):
            await tx.update(2, tx_id, category_id=a_cat)
        await tx.update(2, tx_id, note="взлом")                              # чужую не тронет
        self.assertEqual((await tx.get(1, tx_id))["note"], "")
        self.assertIsNone(await self.repos.goals.get(2, 1))
        with self.assertRaises(PermissionError):
            await self.repos.budgets.set_limit(2, a_cat, 1000)

    async def test_balances_and_transfer(self):
        tx, accounts = self.repos.transactions, self.repos.accounts
        cash, card = [a["id"] for a in await accounts.list(1)]
        await accounts.set_initial(1, cash, 100_000)
        await tx.add(1, "income", 500_000, card, date(2026, 10, 1))
        await tx.add(1, "expense", 120_000, card, date(2026, 10, 2))
        await tx.add(1, "transfer", 200_000, card, date(2026, 10, 3), to_account_id=cash)
        balances = {a["id"]: a["balance"] for a in await accounts.list(1)}
        self.assertEqual(balances[card], 500_000 - 120_000 - 200_000)
        self.assertEqual(balances[cash], 100_000 + 200_000)
        self.assertEqual(await accounts.total(1), 480_000)
        # переводы не попадают в доходы/расходы
        self.assertEqual(await tx.totals(1, date(2026, 10, 1), date(2026, 10, 31)), (500_000, 120_000))

    async def test_aggregates(self):
        tx = self.repos.transactions
        acc = await self._acc(1)
        cats = {c["name"]: c["id"] for c in await self.repos.categories.list(1, "expense")}
        inc = {c["name"]: c["id"] for c in await self.repos.categories.list(1, "income")}
        await tx.add(1, "expense", 30000, acc, date(2026, 9, 5), category_id=cats["Продукты"])
        await tx.add(1, "expense", 70000, acc, date(2026, 10, 5), category_id=cats["Продукты"])
        await tx.add(1, "expense", 5000, acc, date(2026, 10, 6), category_id=cats["Транспорт"])
        await tx.add(1, "income", 900000, acc, date(2026, 10, 1), category_id=inc["Зарплата"])
        await tx.add(1, "income", 100000, acc, date(2026, 10, 2), category_id=inc["Аренда"])
        start, end = date(2026, 10, 1), date(2026, 10, 31)
        rows = await tx.by_category(1, "expense", start, end)
        self.assertEqual([(r["name"], r["total"]) for r in rows], [("Продукты", 70000), ("Транспорт", 5000)])
        self.assertEqual(await tx.passive_income(1, start, end), 100000)
        monthly = await tx.monthly(1, date(2026, 9, 1), end)
        self.assertEqual(monthly["2026-09"], (0, 30000))
        self.assertEqual(monthly["2026-10"], (1000000, 75000))
        self.assertEqual(await tx.count(1, start, end, kind="expense"), 2)
        self.assertEqual(len(await tx.list(1, start, end, search="продукт")), 1)
        # LIKE-символы не работают как шаблон
        self.assertEqual(await tx.list(1, start, end, search="%"), [])

    async def test_goals_and_plan(self):
        goals = self.repos.goals
        a = await goals.add(1, "Подушка", "🛡", target=1_000_000, percent=10)
        b = await goals.add(1, "Отпуск", "🏖", percent=5)
        shares = await goals.distribute(1, 1_000_000, date(2026, 10, 1), tx_id=77)
        self.assertEqual(dict(shares), {a: 100_000, b: 50_000})
        self.assertEqual(await goals.distribute(1, 1_000_000, date(2026, 10, 1), tx_id=77), [])
        self.assertEqual(await goals.total_saved(1), 150_000)
        self.assertFalse(await goals.add_op(1, a, -999_999, date(2026, 10, 1)))   # больше, чем есть
        self.assertTrue(await goals.add_op(1, a, -50_000, date(2026, 10, 1)))
        self.assertEqual((await goals.get(1, a))["saved"], 50_000)
        self.assertFalse(await goals.add_op(2, a, 100, date(2026, 10, 1)))        # чужая копилка
        self.assertEqual(await goals.plan_percent(1), 15)

    async def test_budget_status(self):
        acc = await self._acc(1)
        cat = (await self.repos.categories.list(1, "expense"))[0]["id"]
        await self.repos.budgets.set_limit(1, cat, 50_000)
        await self.repos.budgets.set_limit(1, cat, 60_000)       # перезапись, не дубль
        await self.repos.budgets.set_limit(1, None, 200_000)
        await self.repos.transactions.add(1, "expense", 45_000, acc, date(2026, 10, 3), category_id=cat)
        statuses = await self.repos.budgets.statuses(1, date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(len(statuses), 2)
        self.assertIsNone(statuses[0][0]["category_id"])          # общий — первым
        self.assertEqual([s for _, s in statuses], [45_000, 45_000])
        self.assertEqual(statuses[1][0]["amount"], 60_000)

    async def test_recurring_due(self):
        acc = await self._acc(1)
        rid = await self.repos.recurring.add(
            1, "expense", 10000, acc, "monthly", 5, date(2026, 10, 5), note="Подписка")
        self.assertEqual(await self.repos.recurring.due(1, date(2026, 10, 4)), [])
        self.assertEqual(len(await self.repos.recurring.due(1, date(2026, 10, 5))), 1)
        self.assertEqual(await self.repos.recurring.due(2, date(2026, 10, 5)), [])
        await self.repos.recurring.toggle(1, rid)
        self.assertEqual(await self.repos.recurring.due(1, date(2026, 10, 5)), [])

    async def test_articles(self):
        art = self.repos.articles
        aid = await art.add(1, "План", "# Заголовок\nтекст")
        self.assertIsNone(await art.get(2, aid))
        await art.update(2, aid, title="чужое")
        self.assertEqual((await art.get(1, aid))["title"], "План")
        await art.update(1, aid, body="новый")
        self.assertEqual((await art.get(1, aid))["body"], "новый")
        self.assertEqual(await art.count(2), 0)
        await art.delete(2, aid)
        self.assertEqual(await art.count(1), 1)

    async def test_delete_user_cascades(self):
        acc = await self._acc(1)
        await self.repos.transactions.add(1, "expense", 100, acc, date(2026, 10, 1))
        await self.repos.goals.add(1, "Цель", percent=5)
        await self.repos.articles.add(1, "Статья", "текст")
        await self.repos.users.delete(1)
        for table in ("accounts", "categories", "sources", "transactions", "goals", "articles"):
            count = await self.db.scalar(f"SELECT COUNT(*) FROM {table} WHERE user_id = 1")
            self.assertEqual(count, 0, table)
        # данные второго пользователя целы
        self.assertEqual(len(await self.repos.accounts.list(2)), 2)

    async def test_category_archive_keeps_history(self):
        acc = await self._acc(1)
        cat = (await self.repos.categories.list(1, "expense"))[0]
        await self.repos.transactions.add(1, "expense", 100, acc, date(2026, 10, 1), category_id=cat["id"])
        await self.repos.categories.archive(1, cat["id"])
        self.assertNotIn(cat["id"], [c["id"] for c in await self.repos.categories.list(1, "expense")])
        rows = await self.repos.transactions.list(1, date(2026, 10, 1), date(2026, 10, 1))
        self.assertEqual(rows[0]["cat_name"], cat["name"])
        again = await self.repos.categories.add(1, "expense", cat["name"], "🛒")
        self.assertEqual(again, cat["id"])


if __name__ == "__main__":
    unittest.main()
