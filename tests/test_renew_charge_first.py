"""Продление списывает деньги до панели.

Раньше ``execute_renewal`` сначала продлевал ключ в панели и в базе, а списывал
только в конце. При балансе на одно продление два почти одновременных нажатия
продлевали оба ключа: второе списание упиралось в баланс уже после панели.
Обработчик ловил отказ и не откатывал сессию, так что продление в базе
коммитилось без оплаты.

Проверяем: при нехватке средств панель не вызывается вовсе; при достатке
списание идёт раньше панели; ветка ``except ServiceError`` в
``complete_key_renewal`` откатывает сессию.

Зависимости ``services/keys.py`` подменяются заглушками, база и сеть не нужны.
Запуск из корня проекта: ``venv/bin/python -m unittest tests.test_renew_charge_first -v``
"""

import ast
import asyncio
import importlib.util
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CALLS: list[str] = []
BALANCE = {"debit_ok": False}
_SAVED: dict = {}
KEYS = None

KEY = {
    "client_id": "c1",
    "email": "k1",
    "server_id": "cl1",
    "expiry_time": 1_900_000_000_000,
    "tariff_id": 1,
    "user_id": 7,
}


class UserId(int):
    pass


class TelegramId(int):
    pass


def _async(name, result=None):
    async def fn(*a, **k):
        CALLS.append(name)
        return result() if callable(result) else result

    return fn


def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


def setUpModule():
    global KEYS
    db = _mod("database")
    db.__path__ = []
    db.__getattr__ = lambda name: _async(name)

    async def update_balance(session, ref, amount):
        CALLS.append("update_balance")
        return 0.0 if BALANCE["debit_ok"] else None

    class CarriedAddons:
        device_limit = None
        traffic_gb = None
        has_any = False

    stubs = {
        "core": _mod("core", __path__=[]),
        "core.settings": _mod("core.settings", __path__=[]),
        "core.settings.tariffs_config": _mod("core.settings.tariffs_config", normalize_tariff_config=lambda t: t),
        "database": db,
        "database.access": _mod("database.access", __path__=[]),
        "database.access.resolution": _mod(
            "database.access.resolution",
            TelegramId=TelegramId,
            UserId=UserId,
            resolve_user_optional=_async("resolve_user", types.SimpleNamespace(id=7)),
        ),
        "database.coupons": _mod("database.coupons", mark_coupon_used=_async("mark_coupon_used")),
        "database.keys": _mod(
            "database.keys",
            get_key_by_user_and_email=_async("get_key_by_user_and_email"),
            lock_owned_key_for_operation=_async("lock_key", lambda: types.SimpleNamespace(**KEY)),
            resolve_key_operation_owner=_async("resolve_owner", 7),
            update_key_post_creation_snapshot=_async("post_creation_snapshot"),
            update_key_renewal_snapshot=_async("renewal_snapshot"),
            invalidate_keys_list=_async("invalidate_keys_list"),
        ),
        "database.servers": _mod(
            "database.servers",
            cluster_name_exists=_async("cluster_name_exists", True),
            get_cluster_name_for_server_name=_async("cluster_for_server"),
        ),
        "database.users": _mod("database.users", get_trial=_async("get_trial")),
        "logger": _mod("logger", logger=types.SimpleNamespace(**{
            lvl: (lambda *a, **k: None) for lvl in ("debug", "info", "warning", "error", "exception")})),
        "services": _mod("services", __path__=[str(ROOT / "services")]),
        "services.addons": _mod("services.addons", CarriedAddons=CarriedAddons,
                                resolve_carried_addons=lambda *a, **k: CarriedAddons()),
        "services.operations": _mod("services.operations", renew_key_in_cluster=_async("renew_in_panel", True)),
        "services.tariffs": _mod("services.tariffs", __path__=[]),
        "services.tariffs.tariff_display": _mod(
            "services.tariffs.tariff_display", GB=1024**3,
            get_effective_limits_for_key=_async("limits", (None, 0))),
    }
    for name in [*stubs, "services.errors", "services.keys"]:
        _SAVED[name] = sys.modules.get(name)
    sys.modules.update(stubs)
    spec = importlib.util.spec_from_file_location("services.keys", ROOT / "services" / "keys.py")
    KEYS = importlib.util.module_from_spec(spec)
    sys.modules["services.keys"] = KEYS
    spec.loader.exec_module(KEYS)
    KEYS.get_tariff_by_id = _async("get_tariff", {"id": 1, "device_limit": None, "traffic_limit": None})
    KEYS.get_key_details = _async("get_key_details", lambda: dict(KEY))
    KEYS.update_balance = update_balance
    KEYS.update_key_expiry = _async("update_key_expiry")
    KEYS.get_balance = _async("get_balance", 0.0)


def tearDownModule():
    for name, mod in _SAVED.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


def _renew():
    return KEYS.execute_renewal(
        session=object(),
        billing_user_id=UserId(7),
        client_id="c1",
        key_email="k1",
        key_server_id="cl1",
        tariff_id=1,
        new_expiry_time=1_910_000_000_000,
        total_gb=0,
        cost=100.0,
        carry_addons=False,
    )


class RenewChargeFirst(unittest.TestCase):
    def setUp(self):
        CALLS.clear()

    def test_insufficient_funds_never_reaches_panel(self):
        from services.errors import InsufficientFundsError

        BALANCE["debit_ok"] = False
        with self.assertRaises(InsufficientFundsError):
            asyncio.run(_renew())
        self.assertNotIn("renew_in_panel", CALLS)
        self.assertNotIn("update_key_expiry", CALLS)

    def test_debit_precedes_panel(self):
        BALANCE["debit_ok"] = True
        asyncio.run(_renew())
        self.assertLess(CALLS.index("update_balance"), CALLS.index("renew_in_panel"))

    def test_flow_rolls_back_on_service_error(self):
        tree = ast.parse((ROOT / "handlers" / "keys" / "renew" / "flow.py").read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == "complete_key_renewal")
        handlers = [h for h in ast.walk(fn) if isinstance(h, ast.ExceptHandler)
                    and isinstance(h.type, ast.Name) and h.type.id == "ServiceError"]
        self.assertTrue(handlers)
        for h in handlers:
            calls = {ast.unparse(c.func) for c in ast.walk(h) if isinstance(c, ast.Call)}
            self.assertIn("session.rollback", calls)


if __name__ == "__main__":
    unittest.main()
