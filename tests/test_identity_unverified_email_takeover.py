"""Неподтверждённая почта не передаёт пароль и сессии тому, кто её подтвердил.

Почту при регистрации с паролем может вписать кто угодно, подтверждения нет.
Раньше вход через Google/Яндекс, вход по коду из письма и слияние по почте
подхватывали такой профиль целиком: пароль и сессии регистранта продолжали
открывать аккаунт настоящего владельца почты.

Файл database/identities.py грузится отдельно, зависимости заглушены: пакет
database при импорте поднимает бота целиком.
"""

import asyncio
import importlib.util
import sys
import types
import unittest

from pathlib import Path

from sqlalchemy import Column, String
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql.dml import Delete

ROOT = Path(__file__).resolve().parent.parent
Base = declarative_base()


class IdentitySession(Base):
    __tablename__ = "identity_sessions"
    id = Column(String, primary_key=True)
    identity_id = Column(String)
    token_hash = Column(String)


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True)
    identity_id = Column(String)


def _stub(name: str, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod
    return mod


async def _noop(*a, **k):
    return None


for pkg in ("core", "database", "database.access", "settings", "utils"):
    _stub(pkg)
_stub("core.client_origin", client_campaign=lambda: None, client_origin=lambda: None)
_stub("core.executor", run_cpu=_noop, run_io=_noop)
_stub("database.identity_sessions")
_stub("database.access.identity_cache", invalidate_identity_cache=_noop)
_stub("database.access.resolution", TelegramId=int, UserId=int, get_user_by_tg_id=_noop)
_stub("database.access.tg_mirror", freeze_legacy_tg_owner=_noop, refresh_tg_mirrors_for_user=_noop, release_tg_mirrors=_noop)
models = _stub("database.models", IdentitySession=IdentitySession, User=User)
models.__getattr__ = lambda name: type(name, (), {})
_stub("database.partners", transfer_partner_user_data=_noop)
_stub("database.users", add_user=_noop, check_user_exists=_noop, invalidate_balance_cache=_noop, invalidate_profile_cache=_noop, update_balance=_noop)
_stub("database.yookassa_autopay", transfer_recurring_user_data=_noop)
_stub("logger", logger=types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None))
_stub("settings.config", API_TOKEN_TTL_DAYS=30)
_stub("utils.client_ip", client_ip=lambda r: "")
_stub("utils.cpu_tasks", check_password=lambda *a: False, hash_password=lambda p: p)

spec = importlib.util.spec_from_file_location("identities_under_test", ROOT / "database/identities.py")
idb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(idb)


class _Result:
    rowcount = 0

    def scalar_one_or_none(self):
        return None

    def scalars(self):
        return types.SimpleNamespace(all=lambda: [])


class FakeSession:
    """Хранит строки identity_sessions и исполняет только их удаление."""

    def __init__(self, rows):
        self.rows = list(rows)

    async def execute(self, stmt):
        if isinstance(stmt, Delete) and stmt.table.name == "identity_sessions":
            victim = stmt.whereclause.right.value
            self.rows = [r for r in self.rows if r[0] != victim]
        return _Result()

    async def scalar(self, *a, **k):
        return None

    async def flush(self):
        pass

    async def refresh(self, obj):
        pass

    def add(self, obj):
        pass

    def tokens(self, identity_id):
        return [t for i, t in self.rows if i == identity_id]


def _identity(iid, *, verified, password=None, email="victim@example.com"):
    return types.SimpleNamespace(
        id=iid, email=email, email_verified=verified, password_hash=password, api_token_hash=None,
        google_sub=None, yandex_sub=None, tg_id=None,
    )


def _patch(**registry):
    async def by_email(session, email):
        return next((i for i in registry.values() if i.email == email), None)

    async def lock(session, *ids):
        return {i: registry[i] for i in ids if i in registry}

    async def issue(session, identity, request=None):
        session.rows.append((identity.id, "fresh"))
        return "fresh"

    idb.get_identity_by_email = by_email
    idb.get_identity_by_id = lambda s, i: _async(registry.get(i))
    idb.get_identity_by_google_sub = lambda s, sub: _async(None)
    idb.get_identity_by_yandex_sub = lambda s, sub: _async(None)
    idb._lock_identities = lock
    idb._lock_identity_channel = _noop
    idb.issue_token_for_identity = issue
    idb.can_merge_identities = lambda *a, **k: _async(True)
    idb.ensure_billing_user_for_identity = lambda *a, **k: _async(1)
    idb._combine_billing_users = _noop


async def _async(value):
    return value


def run(coro):
    return asyncio.run(coro)


class UnverifiedEmailTakeover(unittest.TestCase):
    def test_google_snimaet_parol_i_sessii_registranta(self):
        squat = _identity("squat", verified=False, password="attacker-hash")
        _patch(squat=squat)
        s = FakeSession([("squat", "attacker-cookie")])
        got = run(idb.get_or_create_identity_for_google(s, "g-sub", "victim@example.com"))
        self.assertIs(got, squat)
        self.assertIsNone(got.password_hash)
        self.assertEqual(s.tokens("squat"), [])

    def test_yandex_snimaet_parol_i_sessii_registranta(self):
        squat = _identity("squat", verified=False, password="attacker-hash")
        _patch(squat=squat)
        s = FakeSession([("squat", "attacker-cookie")])
        got = run(idb.get_or_create_identity_for_yandex(s, "y-sub", "victim@example.com"))
        self.assertIsNone(got.password_hash)
        self.assertEqual(s.tokens("squat"), [])

    def test_vhod_po_kodu_ostavlyaet_tolko_novuyu_sessiyu(self):
        squat = _identity("squat", verified=False, password="attacker-hash")
        _patch(squat=squat)
        s = FakeSession([("squat", "attacker-cookie")])
        identity, token = run(idb.login_by_verified_email(s, "victim@example.com"))
        self.assertIsNone(identity.password_hash)
        self.assertEqual(s.tokens("squat"), ["fresh"])

    def test_sliyanie_ne_perenosit_parol_i_sessii(self):
        squat = _identity("squat", verified=False, password="attacker-hash")
        owner = _identity("owner", verified=False, email=None)
        _patch(squat=squat, owner=owner)
        s = FakeSession([("squat", "attacker-cookie"), ("owner", "owner-cookie")])
        moved = {}

        async def transfer(session, source, target):
            moved["tokens"] = session.tokens(source.id)
            moved["password"] = source.password_hash

        idb._transfer_identity_data = transfer
        got = run(idb.attach_email(s, "owner", "victim@example.com", verified=True))
        self.assertIsNone(got.password_hash)
        self.assertEqual(moved, {"tokens": [], "password": None})
        self.assertEqual(s.tokens("owner"), ["owner-cookie"])

    def test_podtverzhdyonnyy_profil_ne_trogaem(self):
        real = _identity("real", verified=True, password="owner-hash")
        _patch(real=real)
        s = FakeSession([("real", "owner-cookie")])
        got = run(idb.get_or_create_identity_for_google(s, "g-sub", "victim@example.com"))
        self.assertEqual(got.password_hash, "owner-hash")
        self.assertEqual(s.tokens("real"), ["owner-cookie"])


if __name__ == "__main__":
    unittest.main()
