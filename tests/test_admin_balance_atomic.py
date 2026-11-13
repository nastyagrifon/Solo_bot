"""Списание и установка баланса админом не затирают параллельное пополнение.

Раньше «списать» читало округлённый кешированный баланс и писало абсолютное
значение: баланс 200, админ списывает 50, между чтением и записью вебхук
оплаты добавляет 500 (стало 700), админ пишет 150 — клиент теряет 500.
«Установить» считало разницу для журнала от того же округлённого чтения.

Проверяем на заглушках: списание идёт относительным ``update_balance``,
нехватка денег не обнуляет баланс, а объясняется админу; установка берёт
точный баланс под блокировкой. Модуль ``handlers/admin/users/users_balance.py``
грузится отдельно, aiogram, sqlalchemy и пакет ``database`` подменены.
Запуск из корня проекта: ``venv/bin/python -m unittest tests.test_admin_balance_atomic -v``
"""

import asyncio
import importlib.util
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_SAVED: dict = {}
MOD = None
DB = {"balance": 0.0, "race": 0.0, "payments": []}


def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


class _Any:
    def __getattr__(self, attr):
        return _Any()

    def __call__(self, *a, **k):
        return _Any()

    def __eq__(self, other):
        return _Any()

    __hash__ = object.__hash__


class _Router:
    def __getattr__(self, attr):
        return lambda *a, **k: (lambda fn: fn)


async def get_balance(session, ref, *, exact=False):
    return DB["balance"] if exact else round(DB["balance"], 1)


def _webhook_topup():
    # Пополнение вебхуком успевает между чтением админа и его записью.
    DB["balance"] += DB["race"]
    DB["race"] = 0.0


async def get_locked_balance(session, ref):
    return DB["balance"]


async def update_balance(session, ref, amount, **k):
    _webhook_topup()
    if amount < 0 and DB["balance"] < -amount:
        return None
    DB["balance"] += amount
    return DB["balance"]


async def set_user_balance(session, ref, value):
    _webhook_topup()
    DB["balance"] = float(value)


async def add_payment(**k):
    DB["payments"].append(k["amount"])


def setUpModule():
    global MOD

    class UserId(int):
        pass

    pkg = lambda n: _mod(n, __path__=[])  # noqa: E731
    stubs = {
        "aiogram": _mod("aiogram", F=_Any(), Router=_Router),
        "aiogram.fsm": pkg("aiogram.fsm"),
        "aiogram.fsm.context": _mod("aiogram.fsm.context", FSMContext=object),
        "aiogram.types": _mod("aiogram.types", CallbackQuery=object, Message=object),
        "sqlalchemy": pkg("sqlalchemy"),
        "sqlalchemy.ext": pkg("sqlalchemy.ext"),
        "sqlalchemy.ext.asyncio": _mod("sqlalchemy.ext.asyncio", AsyncSession=object),
        "database": _mod("database", __path__=[], get_balance=get_balance,
                         set_user_balance=set_user_balance, update_balance=update_balance),
        "database.access": pkg("database.access"),
        "database.access.resolution": _mod("database.access.resolution", UserId=UserId,
                                           resolve_user_optional=None),
        "database.payments": _mod("database.payments", add_payment=add_payment,
                                  count_balance_activity=None, get_balance_activity=None),
        "database.users": _mod("database.users", get_locked_balance=get_locked_balance),
        "filters": pkg("filters"),
        "filters.admin": _mod("filters.admin", IsAdminFilter=_Any),
        "utils": pkg("utils"),
        "utils.csv_export": _mod("utils.csv_export", export_user_all_payments_csv=None),
        "handlers": pkg("handlers"),
        "handlers.admin": pkg("handlers.admin"),
        "handlers.admin.panel": pkg("handlers.admin.panel"),
        "handlers.admin.panel.headers": _mod("handlers.admin.panel.headers", card=None, quote=None,
                                             section=None, menu_text=lambda *a: " ".join(a)),
        "handlers.admin.users": pkg("handlers.admin.users"),
        "handlers.admin.users.keyboard": _mod("handlers.admin.users.keyboard", AdminUserEditorCallback=_Any(),
                                              build_users_balance_change_kb=lambda *a: None,
                                              build_users_balance_kb=None),
        "handlers.admin.users.users_states": _mod("handlers.admin.users.users_states", UserEditorState=_Any()),
    }
    name = "handlers.admin.users.users_balance"
    for n in [*stubs, name]:
        _SAVED[n] = sys.modules.get(n)
    sys.modules.update(stubs)
    spec = importlib.util.spec_from_file_location(name, ROOT / "handlers" / "admin" / "users" / "users_balance.py")
    MOD = importlib.util.module_from_spec(spec)
    sys.modules[name] = MOD
    spec.loader.exec_module(MOD)
    MOD.UserId = UserId

    async def _noop(*a, **k):
        return None

    MOD.handle_balance_change = _noop


def tearDownModule():
    for n, m in _SAVED.items():
        if m is None:
            sys.modules.pop(n, None)
        else:
            sys.modules[n] = m


class _State:
    def __init__(self, op):
        self.data = {"user_id": 7, "op_type": op}

    async def get_data(self):
        return self.data

    async def clear(self):
        return None


class _Message:
    def __init__(self, text):
        self.text = text
        self.replies = []

    async def answer(self, text=None, **k):
        self.replies.append(text)


class _Callback:
    def __init__(self):
        self.alerts = []

    async def answer(self, text=None, **k):
        self.alerts.append(text)


def _reset(balance, race=0.0):
    DB.update(balance=balance, race=race, payments=[])


class AdminBalanceAtomic(unittest.TestCase):
    def _input(self, op, text):
        msg = _Message(text)
        asyncio.run(MOD.handle_balance_input(msg, _State(op), None))
        return msg

    def test_take_keeps_parallel_topup(self):
        _reset(200.0, race=500.0)
        self._input("take", "50")
        self.assertEqual(DB["balance"], 650.0)
        self.assertEqual(DB["payments"], [-50])

    def test_quick_take_keeps_parallel_topup(self):
        _reset(200.0, race=500.0)
        cb = _Callback()
        data = types.SimpleNamespace(user_id=7, data=-50)
        asyncio.run(MOD.handle_balance_add(cb, data, None, None))
        self.assertEqual(DB["balance"], 650.0)
        self.assertEqual(DB["payments"], [-50])

    def test_take_more_than_balance_is_refused(self):
        _reset(30.0)
        msg = self._input("take", "50")
        self.assertEqual(DB["balance"], 30.0)
        self.assertEqual(DB["payments"], [])
        self.assertIn("Не списать", msg.replies[-1])

    def test_set_logs_exact_delta(self):
        _reset(99.97)
        self._input("set", "100")
        self.assertEqual(DB["balance"], 100.0)
        self.assertEqual(len(DB["payments"]), 1)
        self.assertAlmostEqual(DB["payments"][0], 0.03, places=6)


if __name__ == "__main__":
    unittest.main()
