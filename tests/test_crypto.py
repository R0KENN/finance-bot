"""Криптовалюты USDT, USDC, BTC, ETH: точность в 8 знаков, курсы, ввод и пересчёт."""
import unittest
from datetime import date
from decimal import Decimal

from aiohttp import web

from finbot.db import Database
from finbot.repos import Repos
from finbot.services import money, rates as rates_module
from finbot.services.currency_switch import switch_base
from finbot.services.parser import parse_quick
from tests.harness import TEST_RATES
from tests.test_flows import FlowCase

NB = " "


class MoneyPrecisionTests(unittest.TestCase):
    def test_sets_and_decimals(self):
        self.assertEqual(set(money.CRYPTO), {"USDT", "USDC", "BTC", "ETH"})
        self.assertTrue(set(money.CRYPTO) <= set(money.CURRENCIES))
        self.assertFalse(set(money.CRYPTO) & set(money.FIAT))
        self.assertEqual([money.decimals_of(c) for c in ("RUB", "USDT", "USDC", "BTC", "ETH")], [2, 2, 2, 8, 8])
        self.assertTrue(money.is_crypto("BTC") and not money.is_crypto("USD"))

    def test_parse_amount_per_currency(self):
        self.assertEqual(money.parse_amount("0,00015", "BTC"), 15_000)
        self.assertEqual(money.parse_amount("0.12345678", "ETH"), 12_345_678)
        self.assertEqual(money.parse_amount("1,5", "BTC"), 150_000_000)
        self.assertEqual(money.parse_amount("2к", "BTC"), 200_000_000_000)
        self.assertIsNone(money.parse_amount("0,123456789", "BTC"))      # девятый знак — слишком точно
        self.assertEqual(money.parse_amount("0,15", "USDT"), 15)
        self.assertIsNone(money.parse_amount("0,001", "USDT"))           # у USDT 2 знака
        self.assertIsNone(money.parse_amount("0,001"))                   # и у рубля тоже
        self.assertIsNone(money.parse_amount("0", "BTC"))
        self.assertIsNone(money.parse_amount("99999999999", "BTC"))      # больше 10 млрд единиц
        self.assertIsNotNone(money.parse_amount("9999999999", "BTC"))    # и это влезает в int64
        self.assertEqual(money.parse_amount("1 500,5"), 150050)          # фиат не изменился

    def test_fmt_crypto_trims_zeros(self):
        self.assertEqual(money.fmt(150_000, "₿", decimals=8), f"0,0015{NB}₿")
        self.assertEqual(money.fmt(100_000_000, "₿", decimals=8), f"1{NB}₿")
        self.assertEqual(money.fmt(5, "₿", decimals=8), f"0,00000005{NB}₿")
        self.assertEqual(money.fmt(-12_345_678_901, "Ξ", decimals=8), f"−123{NB}Ξ".replace("123", "123,45678901"))
        self.assertEqual(money.fmt(12_345_678, "₽"), f"123{NB}456,78{NB}₽")   # фиат: как раньше
        self.assertEqual(money.fmt(150, "₮"), f"1,50{NB}₮")

    def test_rescale_and_major(self):
        self.assertEqual(money.rescale(100, "RUB", "BTC"), 100_000_000)       # 1,00 → 1 BTC (число то же)
        self.assertEqual(money.rescale(100_000_000, "BTC", "RUB"), 100)
        self.assertEqual(money.format_major(Decimal("0.00015")), "0,00015")
        self.assertEqual(money.format_major(Decimal("1500.50")), f"1{NB}500,5")
        self.assertEqual(money.parse_major("0,00015"), Decimal("0.00015"))
        self.assertIsNone(money.parse_major("abc"))


class ParserCryptoTests(unittest.TestCase):
    today = date(2026, 10, 1)

    def test_aliases(self):
        cases = {
            "+0,001 btc фриланс": ("BTC", 100_000, "фриланс"), "0,5 eth": ("ETH", 50_000_000, ""),
            "100 usdt обед": ("USDT", 10_000, "обед"), "+20 usdc": ("USDC", 2_000, ""),
            "1500 ₿": ("BTC", 150_000_000_000, ""), "10 Ξ": ("ETH", 1_000_000_000, ""),
            "0,01 биткоин кофе": ("BTC", 1_000_000, "кофе"), "2 эфир": ("ETH", 200_000_000, ""),
        }
        for text, (code, amount, rest) in cases.items():
            entry = parse_quick(text, self.today)
            self.assertEqual((entry.currency, entry.amount, entry.text), (code, amount, rest), text)

    def test_too_precise_amount_is_flagged_not_rounded(self):
        entry = parse_quick("кофе 0,001", self.today)
        self.assertIsNone(entry.amount)                                   # рублей так не бывает
        self.assertEqual(entry.value, Decimal("0.001"))
        self.assertIsNone(parse_quick("+0,001 usdt", self.today).amount)  # у USDT тоже два знака
        self.assertEqual(parse_quick("+0,001 btc", self.today).amount, 100_000)


class RatesConversionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        self.repos = Repos(self.db)
        await self.repos.rates.set_table(dict(TEST_RATES))
        await self.repos.ensure_user(1, "А", None)
        self.rates = self.repos.rates

    async def asyncTearDown(self):
        await self.db.close()

    async def test_btc_rub_conversion_respects_decimals(self):
        self.assertEqual(await self.rates.convert(1, 100_000, "BTC", "RUB"), 600_000)       # 0,001 BTC = 6 000 ₽
        self.assertEqual(await self.rates.convert(1, 600_000, "RUB", "BTC"), 100_000)
        self.assertEqual(await self.rates.convert(1, 1, "BTC", "RUB"), 6)                   # 1 сатоши = 0,06 ₽
        self.assertEqual(await self.rates.convert(1, 10_000_000, "ETH", "RUB"), 2_500_000)  # 0,1 ETH = 25 000 ₽
        self.assertEqual(await self.rates.to_base(1, 5_000, "USDT"), 450_000)               # 50 USDT = 4 500 ₽

    async def test_cross_between_crypto_and_fiat(self):
        # 0,01 BTC = 60 000 ₽ = 666,67 $ (курс 90)
        self.assertEqual(await self.rates.convert(1, 1_000_000, "BTC", "USD"), 66_667)
        self.assertEqual(await self.rates.convert(1, 100, "ETH", "BTC"), 4)                  # 0,000001 ETH → 0,00000004 BTC

    async def test_override_for_crypto(self):
        await self.rates.set_override(1, "BTC", Decimal("7000000"))
        self.assertEqual(await self.rates.convert(1, 100_000, "BTC", "RUB"), 700_000)
        self.assertEqual(await self.rates.describe(1, "BTC"), (Decimal("7000000"), "свой"))

    async def test_switch_base_scales_crypto_transactions(self):
        profile = await self.repos.users.get(1)
        btc = await self.repos.accounts.add(1, "Биткоин", "₿", 0, "BTC")
        await self.repos.transactions.add(1, "income", 100_000, btc, date(2026, 10, 1))    # 0,001 BTC
        self.assertEqual((await self.repos.transactions.totals(1, date(2026, 1, 1), date(2026, 12, 31)))[0], 600_000)
        await switch_base(self.repos, profile, "USD")
        income = (await self.repos.transactions.totals(1, date(2026, 1, 1), date(2026, 12, 31)))[0]
        self.assertEqual(income, 6_667)                                                     # 66,67 $
        self.assertEqual(await self.repos.accounts.total(1), 6_667)

    async def test_crypto_cannot_be_base(self):
        profile = await self.repos.users.get(1)
        with self.assertRaises(ValueError):
            await switch_base(self.repos, profile, "BTC")
        self.assertEqual((await self.repos.users.get(1)).currency, "RUB")

    async def test_csv_precision(self):
        from finbot.services.exporter import build_csv
        profile = await self.repos.users.get(1)
        btc = await self.repos.accounts.add(1, "BTC-кошелёк", "₿", 0, "BTC")
        await self.repos.transactions.add(1, "expense", 15_000, btc, date(2026, 10, 1))
        data = (await build_csv(self.repos, profile)).decode("utf-8-sig")
        self.assertIn("-0,00015000;BTC;-900,00;RUB", data)


class CryptoFetchTests(unittest.IsolatedAsyncioTestCase):
    """Источники курсов проверяем на локальном сервере, без интернета."""

    async def asyncSetUp(self):
        self.gecko: dict | Exception = {}
        self.coinbase: dict[str, str] = {}
        self.calls: list[str] = []
        app = web.Application()
        app.router.add_get("/gecko", self.handle_gecko)
        app.router.add_get("/coinbase", self.handle_coinbase)
        app.router.add_get("/cbr", self.handle_cbr)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.base = f"http://127.0.0.1:{port}"
        self.saved = (rates_module.COINGECKO_URL, rates_module.COINBASE_URL, list(rates_module.SOURCES))
        rates_module.COINGECKO_URL = f"{self.base}/gecko"
        rates_module.COINBASE_URL = f"{self.base}/coinbase"
        rates_module.SOURCES[:] = [f"{self.base}/cbr"]

    async def asyncTearDown(self):
        rates_module.COINGECKO_URL, rates_module.COINBASE_URL = self.saved[0], self.saved[1]
        rates_module.SOURCES[:] = self.saved[2]
        await self.runner.cleanup()

    async def handle_gecko(self, request):
        self.calls.append("gecko")
        if isinstance(self.gecko, Exception):
            raise web.HTTPServiceUnavailable()
        return web.json_response(self.gecko)

    async def handle_coinbase(self, request):
        code = request.query["currency"]
        self.calls.append(f"coinbase:{code}")
        if code not in self.coinbase:
            raise web.HTTPNotFound()
        return web.json_response({"data": {"rates": {"RUB": self.coinbase[code]}}})

    async def handle_cbr(self, request):
        return web.json_response({"Valute": {"USD": {"Nominal": 1, "Value": 90.5},
                                             "KZT": {"Nominal": 100, "Value": 19.0}}})

    async def test_coingecko_one_request_for_all(self):
        self.gecko = {"tether": {"rub": 90}, "usd-coin": {"rub": 90.1}, "bitcoin": {"rub": 6e6},
                      "ethereum": {"rub": 250000}}
        result = await rates_module.fetch_crypto()
        self.assertEqual(result, {"USDT": 90.0, "USDC": 90.1, "BTC": 6e6, "ETH": 250000.0})
        self.assertEqual(self.calls, ["gecko"])

    async def test_coinbase_fills_the_gaps(self):
        self.gecko = {"bitcoin": {"rub": 6e6}}
        self.coinbase = {"USDT": "89.9", "USDC": "90.2", "ETH": "251000.5"}
        result = await rates_module.fetch_crypto()
        self.assertEqual(result["BTC"], 6e6)
        self.assertEqual((result["USDT"], result["ETH"]), (89.9, 251000.5))
        self.assertNotIn("coinbase:BTC", self.calls)                          # то, что есть, не перезапрашиваем

    async def test_gecko_down_all_from_coinbase(self):
        self.gecko = RuntimeError()
        self.coinbase = {"USDT": "90", "USDC": "90", "BTC": "6100000", "ETH": "250000"}
        self.assertEqual((await rates_module.fetch_crypto())["BTC"], 6_100_000.0)

    async def test_nothing_available_raises(self):
        self.gecko = RuntimeError()
        with self.assertRaises(RuntimeError):
            await rates_module.fetch_crypto()

    async def test_fetch_all_merges_fiat_and_crypto(self):
        self.gecko = {"bitcoin": {"rub": 6e6}}
        result = await rates_module.fetch_all()
        self.assertEqual((result["USD"], result["BTC"]), (90.5, 6e6))
        self.assertAlmostEqual(result["KZT"], 0.19)

    async def test_fetch_all_survives_one_source_failing(self):
        rates_module.SOURCES[:] = [f"{self.base}/missing"]                      # ЦБ недоступен
        self.gecko = {"bitcoin": {"rub": 6e6}}
        result = await rates_module.fetch_all()
        self.assertEqual(result, {"BTC": 6e6})                                  # крипта всё равно обновилась
        rates_module.SOURCES[:] = [f"{self.base}/cbr"]
        self.gecko = RuntimeError()
        self.assertIn("USD", await rates_module.fetch_all())                    # и наоборот

    async def test_everything_down_raises(self):
        rates_module.SOURCES[:] = [f"{self.base}/missing"]
        self.gecko = RuntimeError()
        with self.assertRaises(RuntimeError):
            await rates_module.fetch_all()

    async def test_refresh_stores_crypto_rates(self):
        db = Database(":memory:")
        await db.connect()
        try:
            self.gecko = {"bitcoin": {"rub": 6e6}, "ethereum": {"rub": 250000}}
            repos = Repos(db)
            self.assertTrue(await repos.rates.refresh())
            await repos.ensure_user(1, "А", None)
            self.assertEqual(await repos.rates.convert(1, 100_000, "BTC", "RUB"), 600_000)
            self.assertEqual(await repos.rates.convert(1, 100, "USD", "RUB"), 9050)
        finally:
            await db.close()


class CryptoFlowTests(FlowCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.onboard()
        self.repos = self.h.repos

    async def accounts(self):
        return {a["currency"]: a for a in await self.repos.accounts.list(1)}

    async def last_tx(self):
        return (await self.repos.transactions.list(1, date(2000, 1, 1), date.today()))[0]

    async def test_base_currency_choice_has_no_crypto(self):
        eve = self.h.client(8, "Ева")
        await eve.say("/start")
        self.assertFalse(any("Bitcoin" in label or "Tether" in label for label in eve.screen.labels()))
        await self.u.press_cb("go", "currencies")
        await self.u.press("Сменить основную")
        self.assertFalse(any(k in " ".join(self.u.screen.labels()) for k in ("Bitcoin", "Ethereum", "Tether", "USD Coin")))
        await self.u.press_cb("curbc", "BTC")                                # подделка кнопки
        self.assertIn("не может быть основной", self.u.text)
        await self.u.press_cb("cur", "BTC", "settings")
        self.assertEqual((await self.repos.users.get(1)).currency, "RUB")

    async def test_new_btc_account_with_initial_balance(self):
        await self.u.press_cb("go", "accounts")
        await self.u.press("Новый счёт")
        await self.u.say("₿ Холодный кошелёк")
        labels = " ".join(self.u.screen.labels())
        for name in ("Bitcoin", "Ethereum", "Tether", "USD Coin"):
            self.assertIn(name, labels)
        await self.u.press("Bitcoin")
        self.assertIn("Сколько на счёте сейчас", self.u.text)
        await self.u.say("0,123456789")                                       # слишком точно
        self.assertIn("не больше 8", self.u.text)
        await self.u.say("0,5")
        account = (await self.accounts())["BTC"]
        self.assertEqual((account["initial"], account["balance"]), (50_000_000, 50_000_000))
        self.assertIn(f"0,5{NB}₿", self.u.text)
        self.assertIn(f"≈3{NB}000{NB}000", self.u.text)                       # 0,5 BTC = 3 млн ₽
        await self.u.press_cb("go", "menu")
        self.assertIn(f"0,5{NB}₿", self.u.text)                               # в меню — по валютам
        self.assertIn(f"3{NB}000{NB}000", self.u.text)                        # и капитал в рублях

    async def test_expense_in_btc_through_currency_step(self):
        await self.u.press("➖ Расход")
        await self.u.say("0,00015")
        self.assertIn("В какой валюте 0,00015", self.u.text)
        await self.u.press("Bitcoin")                                         # счёта в BTC нет — заведётся
        self.assertIn(f"0,00015{NB}₿", self.u.text)
        await self.u.press("Прочее")
        self.assertIn(f"≈900{NB}₽", self.u.text)
        await self.u.press("Сохранить")
        row = await self.last_tx()
        self.assertEqual((row["acc_currency"], row["amount"], row["base_amount"]), ("BTC", 15_000, 90_000))
        self.assertIn(f"0,00015{NB}₿", self.u.text)

    async def test_amount_too_precise_for_fiat_is_refused_at_currency_step(self):
        await self.u.press("➖ Расход")
        await self.u.say("0,00015")
        await self.u.press("Рубль")
        self.assertTrue(any("не больше 2 знаков" in a for a in self.h.session.alerts))
        self.assertIn("В какой валюте", self.u.text)                          # остались на выборе
        await self.u.press("Tether")                                          # у USDT тоже 2 знака
        self.assertIn("В какой валюте", self.u.text)
        await self.u.press("Ethereum")
        self.assertIn("категорию", self.u.text)
        self.assertIn(f"0,00015{NB}Ξ", self.u.text)

    async def test_currency_written_with_amount(self):
        await self.u.press("➖ Расход")
        await self.u.say("0,001 btc")
        self.assertNotIn("В какой валюте", self.u.text)
        self.assertIn(f"0,001{NB}₿", self.u.text)
        await self.u.press("Прочее")
        await self.u.press("Сохранить")
        self.assertEqual((await self.last_tx())["amount"], 100_000)

    async def test_written_currency_with_too_many_digits(self):
        await self.u.press("➖ Расход")
        await self.u.say("0,001 usdt")
        self.assertIn("не больше 2 знаков", self.u.text)
        await self.u.say("5 usdt")                                            # можно тут же исправиться
        self.assertIn("категорию", self.u.text)

    async def test_card_currency_change_keeps_the_number(self):
        await self.u.press("➖ Расход")
        await self.u.say("20")
        await self.u.press("Рубль")
        await self.u.press("Прочее")
        await self.u.press("Валюта")
        await self.u.press("Ethereum")
        self.assertIn(f"20{NB}Ξ", self.u.text)
        await self.u.press("Сохранить")
        row = await self.last_tx()
        self.assertEqual((row["acc_currency"], row["amount"], row["base_amount"]), ("ETH", 2_000_000_000, 500_000_000))

    async def test_quick_income_in_btc(self):
        await self.u.say("+0,002 btc фриланс")
        self.assertIn("Записано", self.u.text)
        row = await self.last_tx()
        self.assertEqual((row["acc_currency"], row["amount"], row["base_amount"], row["cat_name"]),
                         ("BTC", 200_000, 1_200_000, "Фриланс и подработка"))
        self.assertIn(f"0,002{NB}₿", self.u.text)
        self.assertIn(f"≈12{NB}000", self.u.text)
        self.assertIn("Создал счёт", self.u.text)

    async def test_quick_usdt_has_two_decimals(self):
        await self.u.say("20 usdt обед")
        row = await self.last_tx()
        self.assertEqual((row["acc_currency"], row["amount"], row["base_amount"]), ("USDT", 2_000, 180_000))
        self.assertIn(f"20{NB}₮", self.u.text)

    async def test_quick_too_many_digits_for_default_currency(self):
        await self.u.say("кофе 0,001")
        self.assertIn("не больше 2 знаков", self.u.text)
        self.assertEqual(await self.h.db.scalar("SELECT COUNT(*) FROM transactions"), 0)

    async def test_bare_decimal_number_keeps_precision_through_the_flow(self):
        await self.u.say("0,00015")
        self.assertIn("что это", self.u.text)
        self.assertIn("0,00015", self.u.text)
        await self.u.press("Расход")
        self.assertIn("В какой валюте 0,00015", self.u.text)
        await self.u.press("Bitcoin")
        await self.u.press("Прочее")
        await self.u.press("Сохранить")
        self.assertEqual((await self.last_tx())["amount"], 15_000)

    async def test_history_edit_amount_in_account_currency(self):
        await self.u.say("+0,002 btc фриланс")
        tx = await self.last_tx()
        await self.u.press_cb("tx", tx["id"])
        self.assertIn(f"0,002{NB}₿", self.u.text)
        await self.u.press("Сумма")
        await self.u.say("0,123456789")
        self.assertIn("не больше 8", self.u.text)
        await self.u.say("0,0025")
        row = await self.repos.transactions.get(1, tx["id"])
        self.assertEqual((row["amount"], row["base_amount"]), (250_000, 1_500_000))
        await self.u.press_cb("go", "hist")
        self.assertIn(f"+0,0025{NB}₿", self.u.text)
        await self.u.press_cb("tx", tx["id"])                                  # пересчёт в основную — в карточке записи
        self.assertIn(f"≈15{NB}000", self.u.text)

    async def test_exchange_rub_to_btc(self):
        btc = await self.repos.accounts.add(1, "BTC", "₿", 0, "BTC")
        await self.u.press_cb("go", "accounts")
        await self.u.press("Перевод")
        await self.u.press("Карта")
        await self.u.press("BTC")
        await self.u.say("60000")
        self.assertIn("Сколько пришло", self.u.text)
        self.assertIn(f"≈ 0,01{NB}₿", " ".join(self.u.screen.labels()))     # 60 000 ₽ по курсу 6 млн
        await self.u.press("по курсу")
        accounts = await self.accounts()
        self.assertEqual(accounts["BTC"]["balance"], 1_000_000)
        self.assertIn(f"60{NB}000{NB}₽ → 0,01{NB}₿", self.u.text)
        # реальный обмен прошёл чуть хуже
        await self.u.press("Перевод")
        await self.u.press("Карта")
        await self.u.press("BTC")
        await self.u.say("30000")
        await self.u.say("0,00495")
        self.assertEqual((await self.accounts())["BTC"]["balance"], 1_000_000 + 495_000)
        self.assertEqual(btc, (await self.accounts())["BTC"]["id"])
        income, expense = await self.repos.transactions.totals(1, date(2000, 1, 1), date.today())
        self.assertEqual((income, expense), (0, 0))

    async def test_exchange_btc_to_rub_received_precision(self):
        await self.repos.accounts.add(1, "BTC", "₿", 100_000_000, "BTC")
        await self.u.press_cb("go", "accounts")
        await self.u.press("Перевод")
        await self.u.press("BTC")
        await self.u.press("Карта")
        await self.u.say("0,001")
        await self.u.say("5999,999")                                         # у рубля копейки, а не тысячные
        self.assertIn("не больше 2", self.u.text)
        await self.u.say("5999,99")
        accounts = await self.accounts()
        self.assertEqual(accounts["BTC"]["balance"], 99_900_000)
        card = next(a for a in await self.repos.accounts.list(1) if a["name"] == "Карта")
        self.assertEqual(card["balance"], 599_999)

    async def test_stats_count_crypto_income_in_base(self):
        await self.u.say("+0,002 btc фриланс")
        await self.u.say("продукты 2000")
        await self.u.press_cb("go", "stats")
        self.assertIn(f"12{NB}000", self.u.text)                              # доходы
        self.assertIn(f"10{NB}000", self.u.text)                              # итог

    async def test_currencies_screen_shows_crypto_rates_and_custom_rate(self):
        await self.repos.accounts.add(1, "BTC", "₿", 0, "BTC")
        await self.u.press_cb("go", "currencies")
        self.assertIn(f"1 ₿ = 6{NB}000{NB}000,00 ₽", self.u.text)
        await self.u.press("Курс BTC")
        await self.u.say("7000000")
        self.assertIn(f"1 ₿ = 7{NB}000{NB}000,00 ₽", self.u.text)
        self.assertIn("свой", self.u.text)
        await self.u.say("+0,001 btc фриланс")
        self.assertEqual((await self.last_tx())["base_amount"], 700_000)

    async def test_recurring_amount_in_default_crypto_account(self):
        btc = await self.repos.accounts.add(1, "BTC", "₿", 0, "BTC")
        await self.repos.users.update(1, default_account_id=btc)
        await self.u.press_cb("go", "recurring")
        await self.u.press("Добавить")
        await self.u.press("Расход")
        await self.u.say("0,0001")
        self.assertIn("категория", self.u.text.lower())
        await self.u.press("Развлечения")
        await self.u.press("Каждый день")
        await self.u.say("Подписка")
        item = (await self.repos.recurring.list(1))[0]
        self.assertEqual((item["amount"], item["acc_currency"]), (10_000, "BTC"))
        self.assertIn(f"0,0001{NB}₿", self.u.text)

    async def test_switch_base_with_btc_account_through_ui(self):
        await self.u.say("+0,001 btc фриланс")
        await self.u.press_cb("go", "currencies")
        await self.u.press("Сменить основную")
        await self.u.press("Доллар")
        await self.u.press("Да, сменить")
        self.assertEqual((await self.repos.users.get(1)).currency, "USD")
        self.assertEqual((await self.last_tx())["base_amount"], 6_667)        # 0,001 BTC = 66,67 $
        self.assertIn(f"1 ₿ = 66{NB}666,67 $", self.u.text)                    # курс показан к новой основной

    async def test_wipe_and_isolation_with_crypto(self):
        bob = self.h.client(2, "Боб")
        await self.onboard(bob)
        await self.u.say("+0,002 btc фриланс")
        await bob.say("кофе 250")
        self.assertEqual(await self.repos.accounts.by_currency(2), {"RUB": -25_000})
        self.assertEqual((await self.repos.accounts.by_currency(1)).get("BTC"), 200_000)


if __name__ == "__main__":
    unittest.main()
