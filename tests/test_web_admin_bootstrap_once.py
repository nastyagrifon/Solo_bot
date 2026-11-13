"""Права веб-админа выдаются на старте, а не на каждом неудачном входе.

Раньше неудачный вход по паролю звал ensure_web_admin: тот находил профиль с
почтой WEB_ADMIN_LOGIN и ставил ему is_admin, не трогая сессии. Если эту почту
заранее зарегистрировал посторонний, его уже выданная cookie становилась
админской после первого же неверного пароля. Заодно bcrypt считался прямо в
event loop на каждую неудачную попытку.

database/setup/web_admin_bootstrap.py грузится отдельно, зависимости заглушены:
пакет database при импорте поднимает бота целиком. Маршрут входа и bootstrap
проверяются разбором исходника (AST), без импорта.
"""

import ast
import asyncio
import importlib.util
import sys
import types
import unittest

from pathlib import Path

from sqlalchemy import Boolean, Column, String
from sqlalchemy.orm import declarative_base

ROOT = Path(__file__).resolve().parent.parent
Base = declarative_base()


class Identity(Base):
    __tablename__ = "identities"
    id = Column(String, primary_key=True)
    email = Column(String)
    password_hash = Column(String)
    is_admin = Column(Boolean)
    onboarding_stage = Column(String)
    onboarding_completed_at = Column(String)


CALLS: list = []


def _stub(name: str, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod
    return mod


async def _run_cpu(fn, *args):
    CALLS.append(("run_cpu", fn.__name__))
    return fn(*args)


async def _delete_other_sessions(session, *, identity_id, keep_token_hash):
    CALLS.append(("delete_sessions", identity_id, keep_token_hash))
    return 1


def _hash_password(password):
    CALLS.append(("hash_password",))
    return "hashed:" + password


for pkg in ("core", "database", "database.setup", "utils"):
    _stub(pkg)
_stub("core.executor", run_cpu=_run_cpu)
_stub("database.identity_sessions", delete_other_sessions=_delete_other_sessions)
_stub("database.models", Identity=Identity)
_stub("logger", logger=types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None))
_stub("utils.cpu_tasks", hash_password=_hash_password)
_stub("settings", config=types.SimpleNamespace(WEB_ADMIN_LOGIN="Admin@Example.org", WEB_ADMIN_PASSWORD="pw"))

spec = importlib.util.spec_from_file_location("web_admin_bootstrap_under_test", ROOT / "database/setup/web_admin_bootstrap.py")
wab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wab)


class _Result:
    def __init__(self, identity):
        self._identity = identity

    def scalar_one_or_none(self):
        return self._identity


class _Session:
    def __init__(self, identity):
        self.identity = identity

    async def execute(self, stmt):
        return _Result(self.identity)

    def add(self, obj):
        self.identity = obj


def _calls_in(path: str, name: str) -> int:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and ((isinstance(node.func, ast.Name) and node.func.id == name) or (isinstance(node.func, ast.Attribute) and node.func.attr == name))
    )


class WebAdminBootstrapTest(unittest.TestCase):
    def setUp(self):
        CALLS.clear()

    def test_promotion_revokes_existing_sessions(self):
        stranger = Identity(id="idn-1", email="admin@example.org", password_hash="x", is_admin=False, onboarding_stage="done")
        asyncio.run(wab.ensure_web_admin(_Session(stranger)))
        self.assertTrue(stranger.is_admin)
        self.assertIn(("delete_sessions", "idn-1", ""), CALLS)

    def test_already_admin_keeps_sessions(self):
        admin = Identity(id="idn-2", email="admin@example.org", password_hash="x", is_admin=True, onboarding_stage="done")
        asyncio.run(wab.ensure_web_admin(_Session(admin)))
        self.assertFalse([c for c in CALLS if c[0] == "delete_sessions"])

    def test_hash_goes_through_executor(self):
        admin = Identity(id="idn-3", email="admin@example.org", password_hash="x", is_admin=True, onboarding_stage="done")
        asyncio.run(wab.ensure_web_admin(_Session(admin)))
        self.assertEqual(CALLS[0], ("run_cpu", "_hash_password"))

    def test_login_route_does_not_bootstrap(self):
        self.assertEqual(_calls_in("api/v2/routes/auth/password.py", "ensure_web_admin"), 0)

    def test_startup_bootstrap_runs_it(self):
        self.assertEqual(_calls_in("core/bootstrap.py", "ensure_web_admin"), 1)


if __name__ == "__main__":
    unittest.main()
