"""Состояния диалогов (FSM). Данные черновиков лежат в state.get_data()."""
from aiogram.fsm.state import State, StatesGroup


class Add(StatesGroup):
    amount = State()
    card = State()      # черновик собран, ждём «Сохранить»; текст здесь — заметка
    note = State()
    date = State()
    new_category = State()
    new_source = State()


class TxEdit(StatesGroup):
    amount = State()
    note = State()
    date = State()


class Hist(StatesGroup):
    search = State()


class BudgetS(StatesGroup):
    amount = State()


class GoalS(StatesGroup):
    name = State()
    target = State()
    percent = State()
    rename = State()
    set_target = State()
    set_percent = State()
    deposit = State()
    withdraw = State()
    distribute = State()


class AccountS(StatesGroup):
    name = State()
    initial = State()
    rename = State()
    set_balance = State()
    transfer = State()
    received = State()


class DictS(StatesGroup):
    add = State()
    rename = State()


class RecurS(StatesGroup):
    amount = State()
    note = State()
    day = State()


class DebtS(StatesGroup):
    person = State()
    amount = State()
    due = State()
    pay = State()


class ArticleS(StatesGroup):
    title = State()
    body = State()
    edit_title = State()
    edit_body = State()
    append = State()


class CurS(StatesGroup):
    rate = State()
