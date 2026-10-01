"""Хомяк-талисман: нужная картинка к нужному событию."""
import hashlib
import random
import unittest
from datetime import date, timedelta

from PIL import Image

from finbot.services import mascot as mascot_module
from finbot.services.mascot import ASSETS, SCENES, Mascot, load, with_mascot
from finbot.ui.common import CAPTION_LIMIT, Screen
from tests.test_flows import FlowCase


def digest(scene: str) -> str:
    return hashlib.md5(load(scene)).hexdigest()


class AssetsTests(unittest.TestCase):
    def test_all_pictures_present_and_sane(self):
        for scene in SCENES:
            path = ASSETS / f"{scene}.jpg"
            self.assertTrue(path.exists(), scene)
            self.assertLess(path.stat().st_size, 400 * 1024, scene)       # Telegram-friendly
            with Image.open(path) as image:
                self.assertEqual((image.format, image.size), ("JPEG", (1024, 1024)), scene)
        self.assertEqual(len({digest(s) for s in SCENES}), len(SCENES))   # все разные

    def test_pools_only_reference_existing_scenes(self):
        for key, pool in mascot_module.POOLS.items():
            self.assertTrue(set(pool) <= set(SCENES), key)
            self.assertTrue(mascot_module.QUIPS.get(key), key)
        for name, pool in mascot_module.EXPENSE_SCENES.items():
            self.assertTrue(set(pool) <= set(SCENES), name)
            self.assertTrue(all(scene in mascot_module.QUIPS for scene in pool), name)


class PickTests(unittest.TestCase):
    def test_no_immediate_repeat(self):
        hamster = Mascot(random.Random(1))
        last = None
        for _ in range(50):
            scene = hamster.pick(7, ["a", "b", "c"])
            self.assertNotEqual(scene, last)
            last = scene

    def test_single_scene_pool_and_users_independent(self):
        hamster = Mascot(random.Random(1))
        self.assertEqual(hamster.pick(1, ["x"]), "x")
        self.assertEqual(hamster.pick(1, ["x"]), "x")          # выбора нет — повтор допустим
        hamster.pick(2, ["a", "b"])
        self.assertIn(hamster.pick(1, ["a", "b"]), ("a", "b"))

    def test_caption_fallback(self):
        class P:
            id, mascot = 1, True

        short = with_mascot(Screen("коротко"), P(), "welcome")
        self.assertIsNotNone(short.photo)
        self.assertLessEqual(len(short.text), CAPTION_LIMIT)
        tight = with_mascot(Screen("x" * (CAPTION_LIMIT - 5)), P(), "welcome")
        self.assertIsNotNone(tight.photo)                        # реплика не влезла — оставили без неё
        self.assertNotIn("<i>", tight.text)
        huge = with_mascot(Screen("x" * (CAPTION_LIMIT + 1)), P(), "welcome")
        self.assertIsNone(huge.photo)                            # не влезает совсем — без картинки

    def test_disabled(self):
        class P:
            id, mascot = 1, False

        screen = Screen("текст")
        self.assertIs(with_mascot(screen, P(), "welcome"), screen)


class MascotFlowTests(FlowCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.onboard()
        self.repos = self.h.repos

    def assertPicture(self, *scenes):
        shown = self.u.screen
        self.assertTrue(shown.photo, "ожидалась картинка")
        self.assertIn(shown.photo_hash, {digest(s) for s in scenes})

    def assertNoPicture(self):
        self.assertFalse(self.u.screen.photo, "картинки быть не должно")

    async def test_welcome_has_hamster(self):
        eve = self.h.client(9, "Ева")
        await eve.say("/start")
        self.assertTrue(eve.screen.photo)
        self.assertEqual(eve.screen.photo_hash, digest("carpet"))
        self.assertIn("выбери валюту", eve.text)
        await eve.press("Рубль")                                  # дальше — обычное меню без картинки
        self.assertFalse(eve.screen.photo)

    async def test_salary_income_contract(self):
        await self.u.say("+50000 зарплата")
        self.assertPicture("contract")
        self.assertIn("Записано", self.u.text)
        self.assertLessEqual(len(self.u.text), CAPTION_LIMIT)
        self.assertIn("<i>", self.u.text)                         # реплика хомяка

    async def test_passive_income_king_or_ledger(self):
        await self.u.say("+1000 дивиденды")
        self.assertPicture("king", "ledger")

    async def test_other_income_and_no_repeat_in_a_row(self):
        seen = []
        for _ in range(6):
            await self.u.say("+500 подарок")
            self.assertPicture("cigar", "carpet", "helicopter")
            seen.append(self.u.screen.photo_hash)
        self.assertTrue(all(a != b for a, b in zip(seen, seen[1:])))

    async def test_every_income_gets_a_picture_via_buttons(self):
        await self.u.press("➕ Доход")
        await self.u.say("1000")
        await self.u.press("Рубль")
        await self.u.press("Прочее")
        await self.u.press("Без источника")
        await self.u.press("Сохранить")
        self.assertTrue(self.u.screen.photo)

    async def test_expense_scenes_by_category(self):
        cases = {"кофе 250": "dinner", "такси 400": "car", "билет 3000": "yacht", "кино 500": "poker"}
        for text, scene in cases.items():
            await self.u.say(text)
            self.assertPicture(scene)
        await self.u.say("продукты 500")                          # обычные траты — без картинки
        self.assertNoPicture()
        self.assertIn("Записано", self.u.text)

    async def test_overspend_is_poker(self):
        cat = next(c for c in await self.repos.categories.list(1, "expense") if c["name"] == "Продукты")
        await self.repos.budgets.set_limit(1, cat["id"], 10_000)
        await self.u.say("продукты 500")                          # 500 ₽ > 100 ₽
        self.assertPicture("poker")
        self.assertIn("исчерпан", self.u.text)

    async def test_distribution_scales(self):
        await self.repos.goals.add(1, "Подушка", "🛡", None, 10)
        await self.u.say("+100000 зарплата")
        await self.u.press("Распределить по плану")
        await self.u.press("✅ Распределить")
        self.assertPicture("scales")
        self.assertIn("Распределено 10", self.u.text)
        self.assertIn("Подушка", self.u.text)
        self.assertIn("К копилкам", " ".join(self.u.screen.labels()))
        await self.u.press("К копилкам")                          # фото -> текст
        self.assertNoPicture()
        self.assertIn("Копилки и план", self.u.text)

    async def test_goal_reached_by_deposit(self):
        goal = await self.repos.goals.add(1, "Отпуск", "🏖", 100_000, 0)
        await self.u.press_cb("gl", goal)
        await self.u.press("Пополнить")
        await self.u.say("500")
        self.assertNoPicture()                                    # цель ещё не достигнута
        await self.u.press("Пополнить")
        await self.u.say("500")
        self.assertPicture("king", "yacht", "helicopter")
        self.assertIn("Цель достигнута", self.u.text)
        await self.u.press("Пополнить")
        await self.u.say("100")
        self.assertNoPicture()                                    # повторно за ту же цель праздника нет

    async def test_goal_reached_by_distribution(self):
        await self.repos.goals.add(1, "Мечта", "✨", 5000, 10)      # 50 ₽, цель маленькая
        await self.u.say("+100000 зарплата")
        await self.u.press("Распределить по плану")
        await self.u.press("✅ Распределить")
        self.assertPicture("king", "yacht", "helicopter")
        self.assertIn("Цель «Мечта» достигнута", self.u.text)

    async def test_debt_closed_contract(self):
        debt = await self.repos.debts.add(1, "owed", "Иван", 100_000)
        await self.u.press_cb("dbo", debt)
        await self.u.press("Погашен полностью")
        self.assertPicture("contract")
        self.assertIn("Закрыт", self.u.text)
        debt2 = await self.repos.debts.add(1, "owed", "Анна", 100_000)
        await self.u.press_cb("dbo", debt2)
        await self.u.press("Частичное")
        await self.u.say("500")
        self.assertNoPicture()                                    # частичное погашение — без праздника
        await self.u.press("Частичное")
        await self.u.say("500")
        self.assertPicture("contract")

    async def test_weekly_digest_ledger(self):
        from finbot import scheduler
        profile = await self.repos.users.get(1)
        await self.repos.transactions.add(1, "expense", 5000, profile.default_account_id,
                                          date.today() - timedelta(days=1))
        await scheduler.send_digest(self.h.bot, self.repos, profile, date.today())
        self.assertPicture("ledger")
        self.assertIn("Итоги недели", self.u.text)

    async def test_toggle_in_settings(self):
        await self.u.press_cb("go", "settings")
        self.assertIn("Картинки с хомяком: <b>включены", self.u.text)
        await self.u.press("Хомяк")
        self.assertIn("выключены", self.u.text)
        self.assertFalse((await self.repos.users.get(1)).mascot)
        await self.u.say("+50000 зарплата")
        self.assertNoPicture()
        self.assertIn("Записано", self.u.text)
        await self.u.press_cb("go", "settings")
        await self.u.press("Хомяк")
        self.assertTrue((await self.repos.users.get(1)).mascot)
        await self.u.say("+50000 зарплата")
        self.assertPicture("contract")

    async def test_buttons_work_on_picture_message(self):
        await self.u.say("+50000 зарплата")
        self.assertTrue(self.u.screen.photo)
        await self.u.press("Ещё доход")                           # с фото на текстовый экран
        self.assertNoPicture()
        self.assertIn("Новый доход", self.u.text)
        await self.u.press("Отмена")
        await self.u.say("+70000 зарплата")
        await self.u.press("Изменить")
        self.assertIn("Доход", self.u.text)
        await self.u.press("Сумма")
        await self.u.say("1000")
        self.assertIn("1 000", self.u.text)
        await self.u.press_cb("go", "menu")
        await self.u.say("+80000 зарплата")
        await self.u.press("Отменить")                            # undo с фото-сообщения
        self.assertIn("Запись отменена", self.u.text)

    async def test_two_users_have_independent_setting(self):
        bob = self.h.client(2, "Боб")
        await self.onboard(bob)
        await self.repos.users.update(1, mascot=0)
        await bob.say("+50000 зарплата")
        self.assertTrue(bob.screen.photo)                         # у Боба хомяк включён
        await self.u.say("+50000 зарплата")
        self.assertFalse(self.u.screen.photo)

    async def test_long_note_does_not_break_caption(self):
        await self.u.press("➕ Доход")
        await self.u.say("1000")
        await self.u.press("Рубль")
        await self.u.press("Зарплата")
        await self.u.press("Без источника")
        await self.u.say("очень длинная заметка " * 14)           # ~300 символов
        await self.u.press("Сохранить")
        self.assertIn("Записано", self.u.text)
        self.assertLessEqual(len(self.u.text), CAPTION_LIMIT)


if __name__ == "__main__":
    unittest.main()
