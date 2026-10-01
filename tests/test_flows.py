"""Сквозные сценарии: пользователь жмёт кнопки и пишет сообщения, как в настоящем Telegram."""
import unittest
from datetime import date, timedelta

from aiogram.exceptions import TelegramBadRequest, TelegramNotFound
from aiogram.methods import SendDocument

from finbot.services.rich import RichState, SendRichMessage
from tests.harness import Harness


class FlowCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness.create()
        self.u = self.h.client(1, "Алиса")
        RichState.enabled = True

    async def asyncTearDown(self):
        violations = list(self.h.session.violations)
        await self.h.close()
        self.assertEqual(violations, [], "нарушены лимиты Telegram")

    async def onboard(self, client=None):
        client = client or self.u
        await client.say("/start")
        await client.press("Рубль")

    async def totals(self, uid=1):
        today = date.today()
        return await self.h.repos.transactions.totals(uid, date(2000, 1, 1), today + timedelta(days=1))


class OnboardingTests(FlowCase):
    async def test_start_and_currency(self):
        await self.u.say("/start")
        self.assertIn("Привет, Алиса", self.u.text)
        await self.u.press("Доллар")
        self.assertIn("Мои финансы", self.u.text)
        self.assertEqual((await self.h.repos.users.get(1)).currency, "USD")

    async def test_menu_has_all_sections(self):
        await self.onboard()
        for label in ("Доход", "Расход", "Статистика", "История", "Копилки", "Бюджеты",
                      "Счета", "Регулярные", "Статьи", "Настройки"):
            self.assertTrue(any(label in b for b in self.u.screen.labels()), label)

    async def test_help_and_cancel(self):
        await self.onboard()
        await self.u.say("/help")
        self.assertIn("Быстрая запись", self.u.text)
        await self.u.say("/cancel")
        self.assertIn("Мои финансы", self.u.text)

    async def test_closed_bot(self):
        from finbot import config
        config.ALLOWED_USERS = {42}
        try:
            await self.u.say("/start")
            self.assertIn("закрытый", self.u.text)
            self.assertIsNone(await self.h.repos.users.get(1))
        finally:
            config.ALLOWED_USERS = set()


class AddFlowTests(FlowCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.onboard()

    async def test_expense_by_buttons(self):
        await self.u.press("➖ Расход")
        self.assertIn("Введи сумму", self.u.text)
        await self.u.say("1 250,50")
        self.assertIn("В какой валюте", self.u.text)             # валюта — сразу после суммы
        await self.u.press("Рубль")
        self.assertIn("категорию", self.u.text)
        await self.u.press("Продукты")
        self.assertIn("1 250,50", self.u.text)
        await self.u.press("Сохранить")
        self.assertIn("Записано", self.u.text)
        self.assertEqual(await self.totals(), (0, 125050))
        # баланс счёта по умолчанию (Карта) уменьшился
        await self.u.press("Меню")
        self.assertIn("−1 250,50", self.u.text)

    async def test_invalid_amount_keeps_prompt(self):
        await self.u.press("➖ Расход")
        await self.u.say("много")
        self.assertIn("Не понял сумму", self.u.text)
        await self.u.say("300")
        await self.u.press("Рубль")
        self.assertIn("категорию", self.u.text)

    async def test_income_with_source_and_new_source(self):
        await self.u.press("➕ Доход")
        await self.u.say("80к")
        await self.u.press("Рубль")
        await self.u.press("Зарплата")
        self.assertIn("источник", self.u.text.lower())
        await self.u.press("➕ Новый источник")
        await self.u.say("🏭 Завод")
        self.assertIn("Завод", self.u.text)
        await self.u.press("Сохранить")
        self.assertIn("Записано", self.u.text)
        rows = await self.h.repos.transactions.list(1, date(2000, 1, 1), date.today())
        self.assertEqual((rows[0]["amount"], rows[0]["src_name"], rows[0]["cat_name"]),
                         (8_000_000, "Завод", "Зарплата"))

    async def test_income_without_source_does_not_loop(self):
        await self.u.press("➕ Доход")
        await self.u.say("1000")
        await self.u.press("Рубль")
        await self.u.press("Прочее")
        await self.u.press("Без источника")
        await self.u.press("Вид")                     # меняем вид, источник не должен спрашиваться снова
        await self.u.press("Подарки")
        self.assertIn("Источник", self.u.text)
        self.assertIn("Сохранить", " ".join(self.u.screen.labels()))

    async def test_change_date_account_note(self):
        await self.u.press("➖ Расход")
        await self.u.say("100")
        await self.u.press("Рубль")
        await self.u.press("Транспорт")
        await self.u.press("Дата")
        await self.u.press("Вчера")
        self.assertIn("Вчера", self.u.text)
        await self.u.press("Счёт")
        await self.u.press("Наличные")
        self.assertIn("Наличные", self.u.text)
        await self.u.press("Заметка")
        await self.u.say("в аэропорт")
        self.assertIn("в аэропорт", self.u.text)
        await self.u.press("Дата")
        await self.u.press("Другая")
        await self.u.say("31.02")
        self.assertIn("Не разобрал дату", self.u.text)
        await self.u.say("15.09.2026" if date.today() >= date(2026, 9, 15) else "01.01.2020")
        await self.u.press("Сохранить")
        row = (await self.h.repos.transactions.list(1, date(2000, 1, 1), date.today()))[0]
        self.assertEqual((row["acc_name"], row["note"]), ("Наличные", "в аэропорт"))

    async def test_text_in_card_becomes_note(self):
        await self.u.press("➖ Расход")
        await self.u.say("100")
        await self.u.press("Рубль")
        await self.u.press("Прочее")
        await self.u.say("подарок маме")
        self.assertIn("подарок маме", self.u.text)

    async def test_new_category(self):
        await self.u.press("➖ Расход")
        await self.u.say("700")
        await self.u.press("Рубль")
        await self.u.press("Новая категория")
        await self.u.say("🎣 Рыбалка")
        self.assertIn("Рыбалка", self.u.text)
        await self.u.press("Сохранить")
        names = [c["name"] for c in await self.h.repos.categories.list(1, "expense")]
        self.assertIn("Рыбалка", names)

    async def test_undo(self):
        await self.u.say("кофе 250")
        self.assertIn("Записано", self.u.text)
        await self.u.press("Отменить")
        self.assertIn("Запись отменена", self.u.text)
        self.assertEqual(await self.totals(), (0, 0))

    async def test_double_save_makes_one_record(self):
        await self.u.press("➖ Расход")
        await self.u.say("100")
        await self.u.press("Рубль")
        await self.u.press("Прочее")
        card = self.u.screen
        await self.u.press("Сохранить")
        await self.u.press_data(next(b.callback_data for b in card.buttons() if "Сохранить" in b.text), card)
        self.assertEqual(await self.h.db.scalar("SELECT COUNT(*) FROM transactions"), 1)
        self.assertTrue(self.h.session.alerts)           # второй тап: «запись уже закрыта»

    async def test_stale_buttons_after_restart(self):
        await self.u.press("➖ Расход")
        await self.u.say("100")
        await self.u.press("Рубль")
        card = self.u.screen
        await self.u.press("Отмена")                      # состояние сброшено
        await self.u.press_data(card.buttons()[0].callback_data, card)
        self.assertTrue(any("Начни заново" in a for a in self.h.session.alerts))
        self.assertIn("Мои финансы", self.u.text)


class CommandsInsideDialogTests(FlowCase):
    """Команды работают из любого диалога и не превращаются в сумму или заметку."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.onboard()

    async def test_commands_during_card(self):
        await self.u.press("➖ Расход")
        await self.u.say("100")
        await self.u.press("Рубль")
        await self.u.press("Прочее")
        await self.u.say("/stats")
        self.assertIn("Расходы", self.u.text)
        self.assertEqual(await self.h.db.scalar("SELECT COUNT(*) FROM transactions"), 0)
        await self.u.say("/add")
        self.assertIn("Что записываем", self.u.text)

    async def test_unknown_command_is_not_a_note(self):
        await self.u.press("➖ Расход")
        await self.u.say("100")
        await self.u.press("Рубль")
        await self.u.press("Прочее")
        await self.u.say("/foo")
        self.assertNotIn("/foo", self.u.text)

    async def test_command_during_amount_prompt(self):
        await self.u.press("➖ Расход")
        await self.u.say("/menu")
        self.assertIn("Мои финансы", self.u.text)
        await self.u.say("кофе 100")                   # состояние сброшено, быстрый ввод снова работает
        self.assertIn("Записано", self.u.text)


class QuickEntryTests(FlowCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.onboard()

    async def test_quick_expense_matches_category(self):
        await self.u.say("кофе 250")
        self.assertIn("Кафе и рестораны", self.u.text)
        self.assertEqual(await self.totals(), (0, 25000))

    async def test_quick_income_and_date(self):
        await self.u.say("+50000 зарплата")
        self.assertIn("Зарплата", self.u.text)
        await self.u.say("такси 400 вчера")
        row = (await self.h.repos.transactions.list(1, date(2000, 1, 1), date.today(), kind="expense"))[0]
        self.assertEqual(row["day"], (date.today() - timedelta(days=1)).isoformat())
        self.assertEqual(await self.totals(), (5_000_000, 40000))

    async def test_bare_number_asks_kind(self):
        await self.u.say("1500")
        self.assertIn("что это", self.u.text)
        await self.u.press("Расход")
        self.assertIn("В какой валюте", self.u.text)
        await self.u.press("Рубль")
        self.assertIn("категорию", self.u.text)
        await self.u.press("Продукты")
        await self.u.press("Сохранить")
        self.assertEqual(await self.totals(), (0, 150000))

    async def test_unknown_category_asks(self):
        await self.u.say("хрень 120")
        self.assertIn("категорию", self.u.text)
        await self.u.press("Прочее")
        await self.u.press("Сохранить")
        row = (await self.h.repos.transactions.list(1, date(2000, 1, 1), date.today()))[0]
        self.assertEqual((row["amount"], row["note"]), (12000, "хрень"))

    async def test_gibberish_gets_hint(self):
        await self.u.say("привет как дела")
        self.assertIn("Не понял", self.u.text)


class HistoryTests(FlowCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.onboard()
        await self.u.say("кофе 250")
        await self.u.say("такси 400")

    async def test_list_open_edit_delete(self):
        await self.u.press("Меню")
        await self.u.press("История")
        self.assertIn("Транспорт", self.u.text)
        await self.u.press("1", exact=True)
        self.assertIn("Расход", self.u.text)
        await self.u.press("Сумма")
        await self.u.say("999")
        self.assertIn("999", self.u.text)
        await self.u.press("Категория")
        await self.u.press("Прочее")
        self.assertIn("Прочее", self.u.text)
        await self.u.press("Заметка")
        await self.u.say("в аэропорт")
        self.assertIn("в аэропорт", self.u.text)
        await self.u.press("Удалить")
        await self.u.press("Да")
        self.assertEqual(await self.totals(), (0, 25000))

    async def test_filters_and_search(self):
        await self.u.say("+1000 подарок")
        await self.u.press("Меню")
        await self.u.press("История")
        await self.u.press("Доходы")
        self.assertIn("Подарки", self.u.text)
        self.assertNotIn("Транспорт", self.u.text)
        await self.u.press("Поиск")
        await self.u.say("кафе")
        self.assertIn("Кафе", self.u.text)
        self.assertNotIn("Транспорт", self.u.text)

    async def test_month_navigation(self):
        await self.u.press("Меню")
        await self.u.press("История")
        await self.u.press("◀️")
        self.assertIn("Здесь пока пусто", self.u.text)
        await self.u.press("▶️")
        self.assertIn("Транспорт", self.u.text)


class StatsTests(FlowCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.onboard()

    async def test_empty_stats_has_no_photo(self):
        await self.u.press("Статистика")
        self.assertFalse(self.u.screen.photo)
        self.assertIn("мало данных", self.u.text)

    async def test_all_charts_render(self):
        await self.u.say("+100000 зарплата")
        await self.u.say("продукты 5000")
        await self.u.say("кофе 300")
        await self.u.press("Меню")
        await self.u.press("Статистика")
        self.assertTrue(self.u.screen.photo)
        self.assertLessEqual(len(self.u.text), 1024)
        for label in ("Источники", "Месяцы", "По дням", "Капитал", "Расходы"):
            await self.u.press(label)
            self.assertTrue(self.u.screen.photo, label)
            self.assertLessEqual(len(self.u.text), 1024, label)
        for period in ("День", "Неделя", "Год", "Всё", "Месяц"):
            await self.u.press(period)
            self.assertTrue(self.u.screen.photo or "мало данных" in self.u.text, period)
        await self.u.press("◀️")
        await self.u.press("Операции")
        self.assertIn("История", self.u.text)

    async def test_summary_numbers(self):
        await self.u.say("+100000 зарплата")
        await self.u.say("продукты 25000")
        await self.u.press("Меню")
        await self.u.press("Статистика")
        self.assertIn("100 000", self.u.text)
        self.assertIn("25 000", self.u.text)
        self.assertIn("75 000", self.u.text)
        self.assertIn("75%", self.u.text)

    async def test_photo_to_text_switch(self):
        await self.u.say("кофе 300")
        await self.u.press("Меню")
        await self.u.press("Статистика")
        self.assertTrue(self.u.screen.photo)
        await self.u.press("Меню")                  # фото -> текст: старое удаляется, новое отправляется
        self.assertFalse(self.u.screen.photo)
        self.assertIn("Мои финансы", self.u.text)

    async def test_stats_command(self):
        await self.u.say("/stats")
        self.assertIn("Расходы", self.u.text)


class BudgetTests(FlowCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.onboard()

    async def test_limit_and_alerts(self):
        await self.u.press("Бюджеты")
        await self.u.press("Лимит на категорию")
        await self.u.press("Продукты")
        await self.u.say("1000")
        self.assertIn("Продукты", self.u.text)
        await self.u.say("продукты 850")
        self.assertIn("использовано 85%", self.u.text)
        await self.u.say("продукты 300")
        self.assertIn("исчерпан", self.u.text)
        await self.u.say("продукты 100")
        self.assertIn("уже превышен", self.u.text)
        await self.u.press("Меню")
        await self.u.press("Бюджеты")
        self.assertIn("превышен на", self.u.text)

    async def test_total_limit_edit_delete(self):
        await self.u.press("Бюджеты")
        await self.u.press("Общий лимит")
        await self.u.say("50000")
        self.assertIn("Общий лимит", self.u.text)
        await self.u.press("⚙️")
        await self.u.press("Удалить")
        self.assertIn("Лимиты помогают", self.u.text)


class GoalsTests(FlowCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.onboard()

    async def test_plan_template_and_distribution(self):
        await self.u.press("Копилки")
        await self.u.press("План")
        await self.u.press("Сбалансированный")
        self.assertIn("Подушка безопасности", self.u.text)
        await self.u.say("+100000 зарплата")
        await self.u.press("Распределить по плану")
        self.assertIn("10 000", self.u.text)
        await self.u.press("✅ Распределить")
        self.assertIn("Распределено 25 000", self.u.text)
        self.assertEqual(await self.h.repos.goals.total_saved(1), 2_500_000)
        # повторно тот же доход распределить нельзя
        tx_id = await self.h.db.scalar("SELECT id FROM transactions WHERE kind='income'")
        await self.u.press_cb("dist", tx_id)
        self.assertTrue(any("уже распределён" in a for a in self.h.session.alerts))

    async def test_create_goal_deposit_withdraw_chart(self):
        await self.u.press("Копилки")
        await self.u.press("Новая копилка")
        await self.u.say("🏖 Отпуск")
        await self.u.say("100к")
        await self.u.say("10")
        self.assertIn("Отпуск", self.u.text)
        self.assertIn("10%", self.u.text)
        await self.u.press("Пополнить")
        await self.u.say("30000")
        self.assertIn("30%", self.u.text)
        await self.u.press("Снять")
        await self.u.say("999999")
        self.assertIn("столько нет", self.u.text)
        await self.u.say("10000")
        self.assertIn("20%", self.u.text)
        await self.u.press("Назад")
        await self.u.press("График")
        self.assertTrue(self.u.screen.photo)
        await self.u.press("Назад")
        await self.u.press("Отпуск")
        await self.u.press("Удалить")
        await self.u.press("Да")
        self.assertEqual(len(await self.h.repos.goals.list(1)), 0)

    async def test_distribute_manual_amount(self):
        await self.h.repos.goals.add(1, "Подушка", "🛡", None, 20)
        await self.u.press("Копилки")
        await self.u.press("Распределить сумму")
        await self.u.say("50000")
        self.assertIn("10 000", self.u.text)
        await self.u.press("✅ Распределить")
        self.assertEqual(await self.h.repos.goals.total_saved(1), 1_000_000)

    async def test_percent_validation(self):
        await self.u.press("Копилки")
        await self.u.press("Новая копилка")
        await self.u.say("Цель")
        await self.u.say("-")
        await self.u.say("150")
        self.assertIn("от 0 до 100", self.u.text)
        await self.u.say("7,5")
        self.assertIn("7.5%", self.u.text)


class AccountsTests(FlowCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.onboard()

    async def test_new_account_transfer_and_fix_balance(self):
        await self.u.press("Счета")
        await self.u.press("Новый счёт")
        await self.u.say("🏦 Сбер")
        await self.u.press("Рубль")
        await self.u.say("15000")
        self.assertIn("Сбер", self.u.text)
        self.assertIn("15 000", self.u.text)
        await self.u.press("Перевод")
        await self.u.press("Сбер")
        await self.u.press("Карта")
        await self.u.say("5000")
        self.assertIn("Переведено", self.u.text)
        balances = {a["name"]: a["balance"] for a in await self.h.repos.accounts.list(1)}
        self.assertEqual((balances["Сбер"], balances["Карта"]), (1_000_000, 500_000))
        await self.u.press("Сбер")
        await self.u.press("Исправить остаток")
        await self.u.say("12000")
        balances = {a["name"]: a["balance"] for a in await self.h.repos.accounts.list(1)}
        self.assertEqual(balances["Сбер"], 1_200_000)

    async def test_cannot_hide_account_with_money(self):
        await self.h.repos.accounts.set_initial(1, (await self.h.repos.accounts.list(1))[0]["id"], 1000)
        await self.u.press("Счета")
        await self.u.press("Наличные")
        await self.u.press("Скрыть")
        self.assertTrue(any("переведи остаток" in a for a in self.h.session.alerts))


class RecurringTests(FlowCase):
    async def test_create_and_apply(self):
        from finbot import scheduler
        await self.onboard()
        await self.u.press("Регулярные")
        await self.u.press("Добавить")
        await self.u.press("Расход")
        await self.u.say("599")
        await self.u.press("Развлечения")
        await self.u.press("Каждый месяц")
        await self.u.press(str(date.today().day) if date.today().day <= 28 else "Последний", exact=date.today().day <= 28)
        await self.u.say("Netflix")
        self.assertIn("Netflix", self.u.text)
        profile = await self.h.repos.users.get(1)
        await scheduler.process_user(self.h.bot, self.h.repos, profile)
        self.assertEqual(await self.totals(), (0, 59900))
        self.assertIn("Добавлено по расписанию", self.u.text)
        await scheduler.process_user(self.h.bot, self.h.repos, profile)       # повтор в тот же день
        self.assertEqual(await self.totals(), (0, 59900))

    async def test_catch_up_missed_days(self):
        from finbot import scheduler
        await self.onboard()
        acc = (await self.h.repos.default_account(await self.h.repos.users.get(1)))["id"]
        start = date.today() - timedelta(days=3)
        await self.h.repos.recurring.add(1, "expense", 1000, acc, "daily", 0, start, note="Парковка")
        created = await scheduler.apply_recurring(self.h.repos, await self.h.repos.users.get(1))
        self.assertEqual(len(created), 4)
        self.assertEqual(await self.totals(), (0, 4000))

    async def test_reminder_and_digest(self):
        from datetime import datetime
        from finbot import scheduler
        await self.onboard()
        await self.h.repos.users.update(1, reminder_hour=20)
        profile = await self.h.repos.users.get(1)
        evening = datetime.now(profile.tz).replace(hour=20, minute=5)
        await scheduler.process_user(self.h.bot, self.h.repos, profile, evening)
        self.assertIn("ещё нет записей", self.u.text)
        calls = len(self.h.session.calls)
        await scheduler.process_user(self.h.bot, self.h.repos, await self.h.repos.users.get(1), evening)
        self.assertEqual(len(self.h.session.calls), calls)              # не повторяется в тот же день
        # дайджест: только если на прошлой неделе что-то было
        self.assertFalse(await scheduler.send_digest(self.h.bot, self.h.repos, profile, date.today()))
        await self.h.repos.transactions.add(1, "expense", 5000, profile.default_account_id,
                                            date.today() - timedelta(days=1))
        self.assertTrue(await scheduler.send_digest(self.h.bot, self.h.repos, profile, date.today()))
        self.assertIn("Итоги недели", self.u.text)


class DictionaryTests(FlowCase):
    async def test_manage_categories_and_sources(self):
        await self.onboard()
        await self.u.press("Настройки")
        await self.u.press("Справочники")
        await self.u.press("Категории расходов")
        await self.u.press("Добавить")
        await self.u.say("🎣 Рыбалка")
        self.assertIn("🎣 Рыбалка", self.u.screen.labels())
        await self.u.press("Рыбалка")
        await self.u.press("Переименовать")
        await self.u.say("🐟 Рыба")
        self.assertIn("🐟 Рыба", self.u.screen.labels())
        await self.u.press("Рыба")
        await self.u.press("Удалить")
        self.assertNotIn("🐟 Рыба", self.u.screen.labels())
        await self.u.press("Назад")
        await self.u.press("Источники")
        await self.u.press("Добавить")
        await self.u.say("Клиент Иванов")
        self.assertIn("🏷 Клиент Иванов", self.u.screen.labels())
        await self.u.press("Назад")
        await self.u.press("Виды доходов")
        await self.u.press("Зарплата")
        await self.u.press("Пассивный")
        self.assertTrue(any("🌱" in label for label in self.u.screen.labels()))


class SettingsTests(FlowCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.onboard()

    async def test_currency_timezone_reminder_digest(self):
        await self.u.press("Настройки")
        await self.u.press("Валюты и курсы")
        await self.u.press("Сменить основную")
        await self.u.press("Евро")
        await self.u.press("Да, сменить")
        self.assertIn("€", self.u.text)
        await self.u.press("Назад")
        await self.u.press("Часовой пояс")
        await self.u.press("Владивосток")
        self.assertIn("Владивосток", self.u.text)
        await self.u.press("Напоминание")
        await self.u.press("21:00")
        self.assertIn("в 21:00", self.u.text)
        await self.u.press("Дайджест")
        self.assertIn("выключен", self.u.text)
        profile = await self.h.repos.users.get(1)
        self.assertEqual((profile.currency, profile.tz_name, profile.reminder_hour, profile.digest),
                         ("EUR", "Asia/Vladivostok", 21, False))

    async def test_export_csv(self):
        await self.u.say("кофе 250")
        await self.u.say("+1000 =cmd|' /C calc'!A0")
        await self.u.press("Прочее")
        await self.u.press("Без источника")
        await self.u.press("Сохранить")
        await self.u.press("Меню")
        await self.u.press("Настройки")
        await self.u.press("Экспорт")
        docs = self.u.sent(SendDocument)
        self.assertEqual(len(docs), 1)
        data = docs[0].document.data.decode("utf-8-sig")
        self.assertIn("Дата;Тип;Сумма", data)
        self.assertIn("Расход;-250,00", data)
        self.assertNotIn(";=cmd", data)           # формулы в ячейках обезврежены

    async def test_wipe(self):
        await self.u.say("кофе 250")
        await self.u.press("Меню")
        await self.u.press("Настройки")
        await self.u.press("Удалить мои данные")
        await self.u.press("Нет")
        self.assertEqual(await self.h.db.scalar("SELECT COUNT(*) FROM transactions"), 1)
        await self.u.press("Удалить мои данные")
        await self.u.press("Да, удалить")
        self.assertIsNone(await self.h.repos.users.get(1))
        self.assertEqual(await self.h.db.scalar("SELECT COUNT(*) FROM transactions"), 0)
        await self.u.say("/start")
        self.assertIn("Привет", self.u.text)


class ArticleTests(FlowCase):
    BODY = "# Итоги\nТекст абзаца.\n- пункт 1\n- пункт 2\n| Статья | Сумма |\n| Еда | 100 |\n---"

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.onboard()

    async def write_article(self):
        await self.u.press("Статьи")
        await self.u.press("Написать")
        await self.u.say("Мой план")
        await self.u.say(self.BODY)
        self.assertIn("Статья сохранена", self.u.text)

    async def test_write_and_read_as_rich_message(self):
        await self.write_article()
        await self.u.press("Открыть")
        rich = self.u.sent(SendRichMessage)
        self.assertEqual(len(rich), 1)
        kinds = [b["type"] for b in rich[0].rich_message["blocks"]]
        self.assertEqual(kinds, ["section_heading", "section_heading", "paragraph", "list", "table", "divider"])
        self.assertEqual(rich[0].rich_message["blocks"][0]["text"], "Мой план")
        buttons = [b.text for row in rich[0].reply_markup.inline_keyboard for b in row]
        self.assertIn("🗑 Удалить", buttons)

    async def test_fallback_to_html_when_rich_rejected(self):
        await self.write_article()
        self.h.session.fail["SendRichMessage"] = TelegramBadRequest(
            SendRichMessage(chat_id=1, rich_message={}), "bad blocks")
        await self.u.press("Открыть")
        self.assertIn("<b>Мой план</b>", self.u.text)
        self.assertIn("<pre>", self.u.text)
        self.assertTrue(RichState.enabled)                 # временный отказ не выключает метод насовсем

    async def test_method_missing_disables_rich(self):
        await self.write_article()
        self.h.session.fail["SendRichMessage"] = TelegramNotFound(
            SendRichMessage(chat_id=1, rich_message={}), "Not Found")
        await self.u.press("Открыть")
        self.assertFalse(RichState.enabled)
        self.assertIn("Мой план", self.u.text)
        before = len(self.u.sent(SendRichMessage))
        await self.u.press_cb("arv", 1)
        self.assertEqual(len(self.u.sent(SendRichMessage)), before)   # больше не пытаемся
        RichState.enabled = True

    async def test_edit_article_from_rich_message_buttons(self):
        await self.write_article()
        await self.u.press("Открыть")
        await self.u.press("Править")                      # правка заменяет сообщение со статьёй
        await self.u.press("Дописать")
        await self.u.say("Ещё абзац")
        await self.u.press("Открыть")
        article = await self.h.repos.articles.get(1, 1)
        self.assertTrue(article["body"].endswith("Ещё абзац"))
        await self.u.press("Править")
        await self.u.press("Заголовок")
        await self.u.say("Новый заголовок")
        self.assertEqual((await self.h.repos.articles.get(1, 1))["title"], "Новый заголовок")

    async def test_delete_article(self):
        await self.write_article()
        await self.u.press("Открыть")
        await self.u.press("Удалить")
        await self.u.press("Да")
        self.assertEqual(await self.h.repos.articles.count(1), 0)

    async def test_incoming_telegram_article_is_saved(self):
        await self.u.say_rich([
            {"type": "section_heading", "text": "Копилка на отпуск"},
            {"type": "paragraph", "text": [{"text": "Откладывай "}, "10% дохода"]},
            {"type": "list", "items": [{"label": "•", "blocks": [{"type": "paragraph", "text": "раз"}]}]},
            {"type": "table", "cells": [[{"text": "a"}, {"text": "b"}]]},
        ])
        self.assertIn("Статья сохранена", self.u.text)
        article = await self.h.repos.articles.get(1, 1)
        self.assertEqual((article["title"], article["origin"]), ("Копилка на отпуск", "imported"))
        self.assertIn("Откладывай 10% дохода", article["body"])
        self.assertIn("| a | b |", article["body"])

    async def test_body_can_be_a_forwarded_article(self):
        await self.u.press("Статьи")
        await self.u.press("Написать")
        await self.u.say("Из Telegram")
        await self.u.say_rich([{"type": "paragraph", "text": "Тело из пересланной статьи"}])
        self.assertEqual((await self.h.repos.articles.get(1, 1))["body"], "Тело из пересланной статьи")

    async def test_month_report_article(self):
        await self.u.say("+100000 зарплата")
        await self.u.say("продукты 25000")
        await self.u.say("кофе 3000")
        await self.u.press("Меню")
        await self.u.press("Статьи")
        await self.u.press("Отчёт за месяц")
        rich = self.u.sent(SendRichMessage)
        self.assertEqual(len(rich), 1)
        kinds = [b["type"] for b in rich[0].rich_message["blocks"]]
        self.assertIn("table", kinds)
        self.assertEqual(len(await self.h.repos.articles.list(1)), 1)
        report = (await self.h.repos.articles.list(1))[0]
        self.assertEqual(report["origin"], "report")
        self.assertIn("Продукты", report["body"])

    async def test_long_article_is_split_in_fallback(self):
        await self.u.press("Статьи")
        await self.u.press("Написать")
        await self.u.say("Длинная")
        await self.u.say("\n\n".join(f"Абзац {i} " + "слово " * 60 for i in range(20)))
        RichState.enabled = False
        try:
            before = len(self.h.session.calls)
            await self.u.press("Открыть")
            sends = [c for c in self.h.session.calls[before:] if type(c).__name__ == "SendMessage"]
            self.assertGreater(len(sends), 1)
            self.assertTrue(all(len(c.text) <= 4096 for c in sends))
            self.assertIsNotNone(sends[-1].reply_markup)       # кнопки только под последней частью
            self.assertTrue(all(c.reply_markup is None for c in sends[:-1]))
        finally:
            RichState.enabled = True


class RichTestCommandTests(FlowCase):
    async def test_richtest_ok_and_error(self):
        await self.onboard()
        await self.u.say("/richtest")
        self.assertIn("работают", self.u.text)
        self.assertEqual(len(self.u.sent(SendRichMessage)), 1)
        self.h.session.fail["SendRichMessage"] = TelegramBadRequest(
            SendRichMessage(chat_id=1, rich_message={}), "unsupported block type")
        await self.u.say("/richtest")
        self.assertIn("не принял", self.u.text)
        self.assertIn("unsupported block type", self.u.text)


class DebtTests(FlowCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.onboard()

    async def test_debt_lifecycle(self):
        await self.u.press("Долги")
        await self.u.press("Мне должны")
        await self.u.say("Иван")
        await self.u.say("15к")
        future = date.today() + timedelta(days=10)
        await self.u.say(future.strftime("%d.%m.%Y"))
        self.assertIn("Иван", self.u.text)
        self.assertIn("15 000", self.u.text)
        await self.u.press("Частичное")
        await self.u.say("20000")
        self.assertIn("Больше остатка", self.u.text)
        await self.u.say("5000")
        self.assertIn("33%", self.u.text)
        await self.u.press("Назад")
        self.assertIn("Мне должны: <b>10 000", self.u.text)
        await self.u.press("Иван")
        await self.u.press("Погашен полностью")
        self.assertIn("Закрыт", self.u.text)                        # карточка закрытого долга (с хомяком)
        await self.u.press("Назад")
        self.assertIn("Мне должны: <b>0", self.u.text)
        self.assertEqual(await self.h.repos.debts.remaining(1), (0, 0))

    async def test_owe_without_due_and_overdue_text(self):
        await self.u.press("Долги")
        await self.u.press("Я должен")
        await self.u.say("Банк")
        await self.u.say("30000")
        await self.u.say("-")
        self.assertIn("Я должен", self.u.text)
        await self.h.db.execute("UPDATE debts SET due_day = ?", ((date.today() - timedelta(days=3)).isoformat(),))
        await self.u.press("Назад")
        self.assertIn("просрочен на 3 дн.", self.u.text)

    async def test_past_due_date_rejected(self):
        await self.u.press("Долги")
        await self.u.press("Мне должны")
        await self.u.say("Пётр")
        await self.u.say("1000")
        await self.u.say("01.01.2020")
        self.assertIn("Прошлое не подойдёт", self.u.text)

    async def test_due_today_reminder(self):
        from datetime import datetime
        from finbot import scheduler
        await self.h.repos.debts.add(1, "owed", "Анна", 500000, date.today())
        profile = await self.h.repos.users.get(1)
        morning = datetime.now(profile.tz).replace(hour=10, minute=1)
        await scheduler.process_user(self.h.bot, self.h.repos, profile, morning)
        self.assertIn("Сегодня срок по долгам", self.u.text)
        self.assertIn("Анна", self.u.text)
        calls = len(self.h.session.calls)
        await scheduler.process_user(self.h.bot, self.h.repos, await self.h.repos.users.get(1), morning)
        self.assertEqual(len(self.h.session.calls), calls)

    async def test_overpay_and_foreign_debt(self):
        debt = await self.h.repos.debts.add(1, "owe", "Банк", 1000)
        self.assertFalse(await self.h.repos.debts.pay(1, debt, 2000))
        self.assertFalse(await self.h.repos.debts.pay(2, debt, 100))
        self.assertTrue(await self.h.repos.debts.pay(1, debt, 1000))
        self.assertEqual((await self.h.repos.debts.get(1, debt))["closed"], 1)


class RunwayTests(FlowCase):
    async def test_runway_line(self):
        await self.onboard()
        await self.u.say("+300000 зарплата")
        await self.u.say("продукты 30000")
        await self.u.press("Меню")
        await self.u.press("Копилки")
        self.assertIn("хватит примерно на", self.u.text)
        self.assertIn("27.0 мес.", self.u.text)         # 270 000 / (30 000 / 3)


class EscapingTests(FlowCase):
    """Названия от пользователя не должны ломать HTML-разметку экранов."""

    EVIL = "<x>&</x>"

    async def test_user_text_cannot_break_html(self):
        await self.onboard()
        r, today = self.h.repos, date.today()
        profile = await r.users.get(1)
        acc = (await r.default_account(profile))["id"]
        account = await r.accounts.add(1, self.EVIL, "💳", 100)
        goal = await r.goals.add(1, self.EVIL, "🎯", 10000, 10)
        cat = await r.categories.add(1, "expense", self.EVIL, "🏷")
        await r.sources.add(1, self.EVIL)
        await r.budgets.set_limit(1, cat, 1000)
        await r.transactions.add(1, "expense", 900, acc, today, category_id=cat, note=self.EVIL)
        debt = await r.debts.add(1, "owed", self.EVIL, 1000, today)
        await r.recurring.add(1, "expense", 100, acc, "daily", 0, today, category_id=cat, note=self.EVIL)
        article = await r.articles.add(1, self.EVIL, f"# {self.EVIL}\n- {self.EVIL}\n| {self.EVIL} | b |")

        for section in ("menu", "accounts", "goals", "budgets", "debts", "recurring", "hist",
                        "stats", "articles", "dicts", "settings"):
            await self.u.press_cb("go", section)
        await self.u.press_cb("acd", account)
        await self.u.press_cb("gl", goal)
        await self.u.press_cb("glp")
        await self.u.press_cb("dbo", debt)
        await self.u.press_cb("bgo", 1)
        await self.u.press_cb("dl", "e")
        await self.u.press_cb("dd", "e", cat)
        await self.u.press_cb("tx", 1)
        await self.u.press_cb("txdel", 1)

        # предупреждение о лимите и результат записи с «злым» названием
        await self.u.press_cb("go", "menu")
        await self.u.say("<x> 500")
        await self.u.press_cb("adc", cat)
        await self.u.press("Сохранить")
        self.assertIn("&lt;x&gt;", self.u.text)

        # статья: и как Rich Message, и в запасном HTML-виде
        await self.u.press_cb("arv", article)
        RichState.enabled = False
        try:
            await self.u.press_cb("arv", article)
        finally:
            RichState.enabled = True
        self.assertIn("&lt;x&gt;", self.u.text)
        self.assertEqual(self.h.session.violations, [])


class IsolationTests(FlowCase):
    async def test_two_users_do_not_see_each_other(self):
        bob = self.h.client(2, "Боб")
        await self.onboard(self.u)
        await self.onboard(bob)
        await self.u.say("+100000 зарплата")
        await self.u.say("кофе 250")
        await bob.say("такси 777")
        self.assertEqual(await self.totals(1), (10_000_000, 25000))
        self.assertEqual(await self.totals(2), (0, 77700))

        await bob.press("Меню")
        await bob.press("История")
        self.assertIn("777", bob.text)
        self.assertNotIn("250", bob.text)
        await bob.press("Меню")
        await bob.press("Статистика")
        self.assertNotIn("100 000", bob.text)

        # Боб подделывает кнопки Алисы: чужая запись, чужая копилка, чужая статья
        alice_tx = await self.h.db.scalar("SELECT id FROM transactions WHERE user_id = 1 LIMIT 1")
        goal_id = await self.h.repos.goals.add(1, "Секрет", "🔒", None, 10)
        article_id = await self.h.repos.articles.add(1, "Приватное", "тайна")
        debt_id = await self.h.repos.debts.add(1, "owed", "Должник", 5000)
        for action, target in (("tx", alice_tx), ("txdely", alice_tx), ("txe", alice_tx),
                               ("gl", goal_id), ("gldel", goal_id), ("gldely", goal_id),
                               ("arv", article_id), ("ardy", article_id), ("are", article_id),
                               ("dist", alice_tx), ("gldo", alice_tx), ("bgd", 1),
                               ("dbo", debt_id), ("dbc", debt_id), ("dbx", debt_id)):
            await bob.press_cb(action, target)
        self.assertEqual(await self.totals(1), (10_000_000, 25000))
        self.assertEqual(len(await self.h.repos.goals.list(1)), 1)
        self.assertEqual(await self.h.repos.articles.count(1), 1)
        self.assertEqual((await self.h.repos.debts.get(1, debt_id))["closed"], 0)
        self.assertEqual(len(bob.sent(SendRichMessage)), 0)
        self.assertNotIn("Секрет", " ".join(s.text for s in self.h.session.screens.values()
                                            if s.chat_id == 2))
        # чужой счёт и категория в черновике Боба
        alice_cat = (await self.h.repos.categories.list(1, "expense"))[0]["id"]
        await bob.press_cb("add", "expense")
        await bob.say("100")
        await bob.press_cb("adc", alice_cat)
        self.assertTrue(any("нет" in a.lower() for a in self.h.session.alerts))

    async def test_wipe_one_user_keeps_other(self):
        bob = self.h.client(2, "Боб")
        await self.onboard(self.u)
        await self.onboard(bob)
        await bob.say("кофе 250")
        await self.u.press("Настройки")
        await self.u.press("Удалить мои данные")
        await self.u.press("Да, удалить")
        self.assertEqual(await self.totals(2), (0, 25000))


class ErrorHandlingTests(FlowCase):
    async def test_unexpected_error_is_reported_not_crashing(self):
        await self.onboard()
        original = self.h.repos.transactions.totals

        async def boom(*args, **kwargs):
            raise RuntimeError("база упала")

        self.h.repos.transactions.totals = boom
        try:
            await self.u.say("/menu")
            self.assertIn("Что-то пошло не так", self.u.text)
        finally:
            self.h.repos.transactions.totals = original
        await self.u.say("/menu")
        self.assertIn("Мои финансы", self.u.text)


if __name__ == "__main__":
    unittest.main()
