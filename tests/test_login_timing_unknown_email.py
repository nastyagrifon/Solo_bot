"""Вход по паролю тратит одинаковое время на известную и неизвестную почту.

Раньше login_by_email сразу возвращал None, если почты нет (или у профиля нет
пароля), а для существующей почты считал bcrypt (~0.25 с). По времени ответа
можно было перебрать, какие почты зарегистрированы.

Файл database/identities.py грузится отдельно, зависимости заглушены: пакет
database при импорте поднимает бота целиком.
"""

import asyncio
import importlib.util
import sys
import types
import unittest

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CALLS: list = []


def _stub(name: str, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod
    return mod


async def _noop(*a, **k):
    return None


async def _run_cpu(fn, *args):
    CALLS.append((fn.__name__, args))
    return fn(*args)


def _check_password(password, password_hash):
    return False


for pkg in ("core", "database", "database.access", "settings", "utils"):
    _stub(pkg)
_stub("core.client_origin", client_campaign=lambda: None, client_origin=lambda: None)
_stub("core.executor", run_cpu=_run_cpu, run_io=_noop)
_stub("database.identity_sessions")
_stub("database.access.identity_cache", invalidate_identity_cache=_noop)
_stub("database.access.resolution", TelegramId=int, UserId=int, get_user_by_tg_id=_noop)
_stub("database.access.tg_mirror", freeze_legacy_tg_owner=_noop, refresh_tg_mirrors_for_user=_noop, release_tg_mirrors=_noop)
models = _stub("database.models")
models.__getattr__ = lambda name: type(name, (), {})
_stub("database.partners", transfer_partner_user_data=_noop)
_stub("database.users", add_user=_noop, check_user_exists=_noop, invalidate_balance_cache=_noop, invalidate_profile_cache=_noop, update_balance=_noop)
_stub("database.yookassa_autopay", transfer_recurring_user_data=_noop)
_stub("logger", logger=types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None))
_stub("settings.config", API_TOKEN_TTL_DAYS=30)
_stub("utils.client_ip", client_ip=lambda r: "")
_stub("utils.cpu_tasks", check_password=_check_password, hash_password=lambda p: p)

spec = importlib.util.spec_from_file_location("identities_login_timing", ROOT / "database/identities.py")
idb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(idb)


def _login(found):
    async def _get(session, email):
        return found

    idb.get_identity_by_email = _get
    return asyncio.run(idb.login_by_email(None, "nobody@example.com", "guess"))


class LoginTimingTest(unittest.TestCase):
    def setUp(self):
        CALLS.clear()

    def _assert_dummy_bcrypt(self):
        self.assertEqual(len(CALLS), 1, "bcrypt должен считаться ровно один раз")
        name, (password, password_hash) = CALLS[0]
        self.assertEqual(name, "_check_password")
        self.assertEqual(password, "guess")
        self.assertTrue(password_hash.startswith("$2b$12$"), password_hash)

    def test_unknown_email_still_runs_bcrypt(self):
        self.assertIsNone(_login(None))
        self._assert_dummy_bcrypt()

    def test_identity_without_password_still_runs_bcrypt(self):
        self.assertIsNone(_login(types.SimpleNamespace(id="idn-1", email="nobody@example.com", password_hash=None)))
        self._assert_dummy_bcrypt()

    def test_dummy_hash_is_valid_bcrypt(self):
        try:
            import bcrypt
        except ImportError:
            self.skipTest("bcrypt не установлен")
        self.assertFalse(bcrypt.checkpw(b"guess", idb._DUMMY_PASSWORD_HASH.encode("ascii")))


if __name__ == "__main__":
    unittest.main()
