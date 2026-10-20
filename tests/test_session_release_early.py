"""Раннее освобождение сессии: успех коммитит, сбой не притворяется успехом.

Раньше упавший коммит проглатывался: обработчик дорабатывал как обычно,
человек видел успешный экран, а данных в базе не было. Проверяем, что ошибка
доходит до вызывающего и что повторный вызов не коммитит дважды.

Это платёжный путь: из двадцати двух мест, которые зовут release_session_early,
два идут прямо перед продлением ключа в панели. Там молчаливый сбой означал бы
продление без оплаты.
Запуск из корня проекта: ``venv/bin/python -m unittest discover -s tests -v``
"""

import asyncio
import importlib.util
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _stub(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


_stub("logger", logger=types.SimpleNamespace(
    **{lvl: (lambda *a, **k: None) for lvl in
       ("debug", "info", "warning", "error", "exception", "critical")}))
_stub("sqlalchemy")
_stub("sqlalchemy.ext")
_stub("sqlalchemy.ext.asyncio", AsyncSession=object, async_sessionmaker=object)
_stub("aiogram", BaseMiddleware=object)
_stub("aiogram.exceptions", TelegramForbiddenError=Exception)
_stub("aiogram.types", TelegramObject=object)
_stub("settings")
_stub("settings.config", LOG_SESSION_DURATION=False)
_stub("core")
_stub("core.redis_cache", cache_purge_pending=lambda *a, **k: None)

_SPEC = importlib.util.spec_from_file_location(
    "session_mw", ROOT / "middlewares" / "session.py"
)
_mod = importlib.util.module_from_spec(_SPEC)
try:
    _SPEC.loader.exec_module(_mod)
    LOADED = True
except Exception as exc:                                             # noqa: BLE001
    LOADED, LOAD_ERROR = False, exc


class FakeSession:
    def __init__(self, fail_commit=False, fail_rollback=False):
        self.fail_commit = fail_commit
        self.fail_rollback = fail_rollback
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    async def commit(self):
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError("база недоступна")

    async def rollback(self):
        self.rollbacks += 1
        if self.fail_rollback:
            raise RuntimeError("и откат не вышел")

    async def close(self):
        self.closed = True


@unittest.skipUnless(LOADED, "модуль не загрузился без полного окружения")
class ReleaseEarly(unittest.TestCase):
    def proxy(self, session):
        return _mod._SessionProxy(session, lambda: session, {})

    def test_success_commits_and_marks_released(self):
        s = FakeSession()
        p = self.proxy(s)
        self.assertTrue(asyncio.run(p.release_early()))
        self.assertEqual(s.commits, 1)
        self.assertEqual(s.rollbacks, 0)

    def test_second_call_does_not_commit_again(self):
        s = FakeSession()
        p = self.proxy(s)
        asyncio.run(p.release_early())
        self.assertFalse(asyncio.run(p.release_early()))
        self.assertEqual(s.commits, 1, "второй вызов не должен коммитить")

    def test_failed_commit_raises_and_rolls_back(self):
        """Главное: сбой не выглядит успехом."""
        s = FakeSession(fail_commit=True)
        p = self.proxy(s)
        with self.assertRaises(RuntimeError):
            asyncio.run(p.release_early())
        self.assertEqual(s.rollbacks, 1)

    def test_failed_commit_leaves_session_unreleased(self):
        """Иначе следующий вызов решит, что всё уже записано."""
        s = FakeSession(fail_commit=True)
        p = self.proxy(s)
        with self.assertRaises(RuntimeError):
            asyncio.run(p.release_early())
        self.assertFalse(p._released)

    def test_failed_rollback_does_not_hide_commit_error(self):
        s = FakeSession(fail_commit=True, fail_rollback=True)
        p = self.proxy(s)
        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(p.release_early())
        self.assertIn("база недоступна", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
