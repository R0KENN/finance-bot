"""Единая точка доступа к данным: repos.users, repos.transactions и так далее."""
import asyncio

from ..db import Database
from ..defaults import (
    ACCOUNTS, EXPENSE_CATEGORIES, INCOME_CATEGORIES, SOURCES,
)
from ..services.rates import RatesService
from .articles import ArticlesRepo
from .budgets import BudgetsRepo
from .debts import DebtsRepo
from .dictionaries import AccountsRepo, CategoriesRepo, SourcesRepo
from .goals import GoalsRepo
from .recurring import RecurringRepo
from .transactions import TransactionsRepo
from .users import Profile, UsersRepo


class Repos:
    def __init__(self, db: Database, rate_fetcher=None):
        self.db = db
        self.rates = RatesService(db, rate_fetcher) if rate_fetcher else RatesService(db)
        self.users = UsersRepo(db)
        self.accounts = AccountsRepo(db, self.rates)
        self.categories = CategoriesRepo(db)
        self.sources = SourcesRepo(db)
        self.transactions = TransactionsRepo(db, self.rates)
        self.goals = GoalsRepo(db)
        self.budgets = BudgetsRepo(db)
        self.debts = DebtsRepo(db)
        self.recurring = RecurringRepo(db)
        self.articles = ArticlesRepo(db)
        # aiogram обрабатывает обновления параллельно: два первых сообщения нового
        # пользователя не должны оба пытаться его создать
        self._signup_lock = asyncio.Lock()

    async def ensure_user(self, user_id: int, first_name: str, username: str | None) -> Profile:
        """Находит пользователя или заводит нового со стартовым набором справочников."""
        profile = await self.users.get(user_id)
        if profile:
            return profile
        async with self._signup_lock:
            profile = await self.users.get(user_id)       # пока ждали — мог создаться
            if profile:
                return profile
            return await self._create_user(user_id, first_name, username)

    async def _create_user(self, user_id: int, first_name: str, username: str | None) -> Profile:
        await self.users.create(user_id, first_name, username)
        for emoji, name, passive in INCOME_CATEGORIES:
            await self.categories.add(user_id, "income", name, emoji, passive)
        for emoji, name in EXPENSE_CATEGORIES:
            await self.categories.add(user_id, "expense", name, emoji)
        for emoji, name in SOURCES:
            await self.sources.add(user_id, name, emoji)
        first_account = None
        for emoji, name in ACCOUNTS:
            account_id = await self.accounts.add(user_id, name, emoji)
            first_account = first_account or account_id
        # Счёт по умолчанию — карта, если есть: расходы чаще всего с неё
        accounts = await self.accounts.list(user_id)
        default = next((a["id"] for a in accounts if a["name"] == "Карта"), first_account)
        await self.users.update(user_id, default_account_id=default)
        return await self.users.get(user_id)

    async def default_account(self, profile: Profile):
        """Счёт по умолчанию; если он удалён — первый из имеющихся (или создаём новый)."""
        accounts = await self.accounts.list(profile.id)
        for account in accounts:
            if account["id"] == profile.default_account_id:
                return account
        if not accounts:
            account_id = await self.accounts.add(profile.id, "Наличные", "💵")
            accounts = await self.accounts.list(profile.id)
            profile.default_account_id = account_id
        await self.users.update(profile.id, default_account_id=accounts[0]["id"])
        profile.default_account_id = accounts[0]["id"]
        return accounts[0]
