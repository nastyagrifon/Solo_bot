"""Проверка «хватает ли денег» идёт по точному балансу.

``get_balance`` отдаёт баланс округлённым до десятых (и так же кладёт в кеш):
это для показа. Проверки перед покупкой и продлением сравнивали цену с ним,
а ``update_balance`` списывает по точному значению. Баланс 99.97 при цене 100
проходил проверку как 100.0, и отказ всплывал уже при списании.

Проверяем: ``get_balance(..., exact=True)`` не берёт округлённый кеш и не
округляет; точки решения в продлении, покупке и чекауте зовут его с
``exact=True``. Модуль ``database/users.py`` грузится отдельно с заглушками,
пакет ``database`` не импортируется.
Запуск из корня проекта: ``venv/bin/python -m unittest tests.test_balance_exact_check -v``
"""

import ast
import asyncio
import importlib.util
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_SAVED: dict = {}
USERS = None


def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


def _any(name):
    m = _mod(name)
    m.__getattr__ = lambda attr: type(attr, (), {})
    return m


async def _const(value, *a, **k):
    return value


def setUpModule():
    global USERS

    class UserId(int):
        pass

    async def resolve_uid_cached(session, ref):
        return 7

    async def cache_get(key):
        return 100.0  # округлённое значение, как его кладёт get_balance

    async def cache_set(*a, **k):
        return None

    stubs = {
        "core": _mod("core", __path__=[]),
        "core.executor": _mod("core.executor", spawn=lambda *a, **k: None),
        "core.redis_cache": _mod("core.redis_cache", cache_delete=cache_set, cache_get=cache_get,
                                 cache_key=lambda *a: "k", cache_set=cache_set),
        "database": _mod("database", __path__=[]),
        "database.access": _mod("database.access", __path__=[]),
        "database.access.resolution": _mod(
            "database.access.resolution", TelegramId=int, UserId=UserId,
            resolve_uid_cached=resolve_uid_cached, resolve_user_optional=resolve_uid_cached,
            user_ref_cache_key=lambda *a: "balance:7", user_ref_cache_keys=lambda *a: []),
        "database.cache_purge": _mod("database.cache_purge", defer_purge=lambda *a, **k: None,
                                     is_purge_pending=lambda *a: False),
        "database.models": _any("database.models"),
        "database.partners": _mod("database.partners", delete_partner_user_data=cache_set),
        "database.yookassa_autopay": _mod("database.yookassa_autopay", delete_recurring_user_data=cache_set),
        "logger": _mod("logger", logger=types.SimpleNamespace(**{
            lvl: (lambda *a, **k: None) for lvl in ("debug", "info", "warning", "error", "exception")})),
        "settings": _mod("settings", __path__=[]),
        "settings.cache_config": _mod("settings.cache_config"),
    }
    stubs["settings.cache_config"].__getattr__ = lambda attr: 60
    for name in [*stubs, "database.users"]:
        _SAVED[name] = sys.modules.get(name)
    sys.modules.update(stubs)
    spec = importlib.util.spec_from_file_location("database.users", ROOT / "database" / "users.py")
    USERS = importlib.util.module_from_spec(spec)
    sys.modules["database.users"] = USERS
    spec.loader.exec_module(USERS)
    # Запрос к базе подменяется: колонка из заглушки моделей не годится для SQL.
    USERS.select = lambda *a, **k: types.SimpleNamespace(where=lambda *a, **k: "stmt")
    USERS.func = types.SimpleNamespace(coalesce=lambda *a: None)
    USERS.User = types.SimpleNamespace(balance=None, id=0)


def tearDownModule():
    for name, mod in _SAVED.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


class _Session:
    async def execute(self, stmt):
        return types.SimpleNamespace(scalar_one_or_none=lambda: 99.97)


def _calls_with_exact(path, func_name):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == func_name)
    calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and ast.unparse(c.func) == "get_balance"]
    return [any(k.arg == "exact" and getattr(k.value, "value", None) is True for k in c.keywords) for c in calls]


class BalanceExactCheck(unittest.TestCase):
    def test_exact_balance_is_not_rounded_up(self):
        value = asyncio.run(USERS.get_balance(_Session(), 7, exact=True))
        self.assertEqual(value, 99.97)
        self.assertLess(value, 100)

    def test_display_balance_stays_rounded(self):
        self.assertEqual(asyncio.run(USERS.get_balance(_Session(), 7)), 100.0)

    def test_decision_points_use_exact(self):
        for path, fn in (
            ("handlers/keys/renew/flow.py", "_finalize_renewal"),
            ("handlers/tariffs/buy/purchase.py", "proceed_purchase_with_values"),
            ("services/payments/checkout_intent.py", "freeze_checkout_intent"),
            ("services/payments/checkout_intent.py", "apply_checkout_intent"),
        ):
            with self.subTest(path=path):
                flags = _calls_with_exact(path, fn)
                self.assertTrue(flags)
                self.assertTrue(all(flags))


if __name__ == "__main__":
    unittest.main()
