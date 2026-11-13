"""Пробный период на сайте выдаётся один раз даже при параллельных запросах.

``POST /trial`` проверял ``get_trial`` и число ключей без блокировки, а флаг
триала ставил после создания ключа: два параллельных запроса оба проходили
проверку и получали по бесплатному пробному периоду.

Проверяем: ``claim_trial`` занимает триал одним ``UPDATE ... WHERE trial IN
(0, -1) RETURNING`` и второй раз отказывает (на sqlite в памяти с настоящим
SQLAlchemy); в ``activate_trial`` занятие стоит перед каждой выдачей ключа.
Модуль ``database/users.py`` грузится отдельно с заглушками, пакет
``database`` не импортируется. Нужен SQLAlchemy 2 (venv бота).
Запуск из корня проекта: ``venv/bin/python -m unittest tests.test_trial_claim_once -v``
"""

import ast
import asyncio
import importlib.util
import pathlib
import sys
import types
import unittest

from sqlalchemy import Column, Integer, create_engine
from sqlalchemy.orm import Session, declarative_base

ROOT = pathlib.Path(__file__).resolve().parent.parent
_SAVED: dict = {}
USERS = None
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    tg_id = Column(Integer)
    trial = Column(Integer)


def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


async def _none(*a, **k):
    return None


def setUpModule():
    global USERS

    class UserId(int):
        pass

    async def resolve_user_optional(session, ref):
        return session.sync.get(User, int(ref))

    models = _mod("database.models", User=User)
    models.__getattr__ = lambda attr: type(attr, (), {})
    stubs = {
        "core": _mod("core", __path__=[]),
        "core.executor": _mod("core.executor", spawn=lambda *a, **k: None),
        "core.redis_cache": _mod("core.redis_cache", cache_delete=_none, cache_get=_none,
                                 cache_key=lambda *a: "k", cache_set=_none),
        "database": _mod("database", __path__=[]),
        "database.access": _mod("database.access", __path__=[]),
        "database.access.resolution": _mod(
            "database.access.resolution", TelegramId=int, UserId=UserId, resolve_uid_cached=_none,
            resolve_user_optional=resolve_user_optional, user_ref_cache_key=lambda *a: "k",
            user_ref_cache_keys=lambda *a: []),
        "database.cache_purge": _mod("database.cache_purge", defer_purge=lambda *a, **k: None,
                                     is_purge_pending=lambda *a: False),
        "database.models": models,
        "database.partners": _mod("database.partners", delete_partner_user_data=_none),
        "database.yookassa_autopay": _mod("database.yookassa_autopay", delete_recurring_user_data=_none),
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


def tearDownModule():
    for name, mod in _SAVED.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


class _AsyncSession:
    """Асинхронная обёртка над синхронной сессией sqlite."""

    def __init__(self, sync):
        self.sync = sync

    async def scalar(self, stmt):
        return self.sync.scalar(stmt)


class TrialClaimOnce(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.db.add_all([User(id=1, trial=0), User(id=2, trial=-1), User(id=3, trial=1), User(id=4, trial=None)])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _claim(self, uid):
        return asyncio.run(USERS.claim_trial(_AsyncSession(self.db), uid))

    def test_second_claim_is_refused(self):
        for uid in (1, 2, 4):
            with self.subTest(uid=uid):
                self.assertTrue(self._claim(uid))
                self.assertFalse(self._claim(uid))
                self.db.expire_all()
                self.assertEqual(self.db.get(User, uid).trial, 1)

    def test_used_trial_is_refused(self):
        self.assertFalse(self._claim(3))

    def test_route_claims_before_each_key(self):
        tree = ast.parse((ROOT / "api" / "v2" / "routes" / "tariffs.py").read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "activate_trial")
        branches = [n for n in fn.body if isinstance(n, ast.If)
                    and any(isinstance(c, ast.Call) and ast.unparse(c.func) == "create_vpn_key_headless"
                            for c in ast.walk(n))]
        self.assertEqual(len(branches), 2)
        for branch in branches:
            calls = [ast.unparse(c.func) for s in branch.body for c in ast.walk(s) if isinstance(c, ast.Call)]
            self.assertIn("claim_trial", calls)
            self.assertLess(calls.index("claim_trial"), calls.index("create_vpn_key_headless"))


if __name__ == "__main__":
    unittest.main()
