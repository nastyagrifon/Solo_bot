#!/usr/bin/env python3
"""Обёртка panels/remnawave_bind привязывает подписку ключа до вызова панели.

Клиент панели ищет пользователя по привязанной ссылке подписки. У части
ключей client_id бота не совпадает с id в панели, и вызов по client_id без
``bind_subscription`` пользователя не находит. Обёртка ставится на класс,
поэтому проверяем её на поддельном классе клиента: порядок вызовов, кеш на
экземпляр, сквозной вызов при чужом id и при ошибке поиска, повторную
установку. База подменена заглушками в sys.modules, пакет database не
импортируется.

Запуск из корня проекта: ``python3 -m unittest tests.test_remnawave_bind_shim -v``
"""

import asyncio
import importlib.util
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

KEYS = {"bot-id": ("user@x", "https://sub.example/abc", "vless://legacy")}
DB = {"queries": 0, "fail": False}


class _Col:
    def __init__(self, name):
        self.name = name

    def in_(self, ids):
        return ("in", list(ids))


class _Stmt:
    def where(self, cond):
        self.ids = cond[1]
        return self


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        DB["queries"] += 1
        if DB["fail"]:
            raise RuntimeError("db down")
        rows = [(cid, *KEYS[cid]) for cid in stmt.ids if cid in KEYS]
        return types.SimpleNamespace(all=lambda: rows)


def _load_shim():
    logger = types.ModuleType("logger")
    logger.logger = types.SimpleNamespace(debug=lambda *a, **k: None, warning=lambda *a, **k: None)
    sqlalchemy = types.ModuleType("sqlalchemy")
    sqlalchemy.select = lambda *cols: _Stmt()
    database = types.ModuleType("database")
    database.__path__ = []
    database.async_session_maker = _Session
    models = types.ModuleType("database.models")
    models.Key = types.SimpleNamespace(**{n: _Col(n) for n in ("client_id", "email", "remnawave_link", "key")})
    stubs = {"logger": logger, "sqlalchemy": sqlalchemy, "database": database, "database.models": models}
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    spec = importlib.util.spec_from_file_location("remnawave_bind_under_test", ROOT / "panels/remnawave_bind.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, saved


def _restore(saved):
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


def _fake_class():
    class FakeAPI:
        def __init__(self):
            self.calls = []

        def bind_subscription(self, client_id, username, subscription_url):
            self.calls.append(("bind", client_id, username, subscription_url))

        async def update_user(self, uuid=None, lookup_username=None, **kw):
            self.calls.append(("update_user", uuid))
            return True

        async def delete_user(self, client_id, username=None):
            self.calls.append(("delete_user", client_id))
            return True

        async def bulk_set_expiry(self, items, username=None, password=None, return_client_ids=False):
            self.calls.append(("bulk_set_expiry", [i[0] for i in items]))
            return [i[0] for i in items]

    return FakeAPI


class BindShim(unittest.TestCase):
    def setUp(self):
        self.shim, self._saved = _load_shim()
        DB["queries"] = 0
        DB["fail"] = False
        self.cls = _fake_class()
        self.assertTrue(self.shim.install(self.cls))

    def tearDown(self):
        _restore(self._saved)

    def run_(self, coro):
        return asyncio.run(coro)

    def test_bind_before_delete_with_email_and_link(self):
        api = self.cls()
        self.assertTrue(self.run_(api.delete_user("bot-id", username="user@x")))
        self.assertEqual(
            api.calls, [("bind", "bot-id", "user@x", "https://sub.example/abc"), ("delete_user", "bot-id")]
        )

    def test_update_user_by_uuid_kwarg(self):
        api = self.cls()
        self.run_(api.update_user(uuid="bot-id", lookup_username="user@x"))
        self.assertEqual(api.calls[0], ("bind", "bot-id", "user@x", "https://sub.example/abc"))
        self.assertEqual(api.calls[1], ("update_user", "bot-id"))

    def test_link_falls_back_to_key(self):
        KEYS["legacy-id"] = ("old@x", None, "https://sub.example/old")
        try:
            api = self.cls()
            self.run_(api.delete_user("legacy-id"))
            self.assertEqual(api.calls[0], ("bind", "legacy-id", "old@x", "https://sub.example/old"))
        finally:
            KEYS.pop("legacy-id")

    def test_second_call_does_not_requery(self):
        api = self.cls()
        self.run_(api.delete_user("bot-id"))
        self.run_(api.update_user(uuid="bot-id"))
        self.assertEqual(DB["queries"], 1)
        self.assertEqual([c[0] for c in api.calls], ["bind", "delete_user", "update_user"])

    def test_explicit_bind_skips_lookup(self):
        api = self.cls()
        api.bind_subscription("bot-id", "user@x", "https://sub.example/abc")
        self.run_(api.delete_user("bot-id"))
        self.assertEqual(DB["queries"], 0)

    def test_unknown_id_calls_through_and_miss_is_cached(self):
        api = self.cls()
        self.run_(api.delete_user("12345"))
        self.run_(api.delete_user("12345"))
        self.assertEqual(api.calls, [("delete_user", "12345"), ("delete_user", "12345")])
        self.assertEqual(DB["queries"], 1)

    def test_lookup_error_still_calls_through(self):
        DB["fail"] = True
        api = self.cls()
        self.assertTrue(self.run_(api.delete_user("bot-id")))
        self.assertEqual(api.calls, [("delete_user", "bot-id")])

    def test_bulk_set_expiry_binds_every_item_first(self):
        KEYS["bot-2"] = ("two@x", "https://sub.example/two", None)
        try:
            api = self.cls()
            items = [("bot-id", "2026-12-01T00:00:00Z", "user@x"), ("bot-2", "2026-12-01T00:00:00Z", "two@x")]
            self.run_(api.bulk_set_expiry(items, username="a", password="b", return_client_ids=True))
            self.assertEqual(DB["queries"], 1)
            self.assertEqual(
                api.calls,
                [
                    ("bind", "bot-id", "user@x", "https://sub.example/abc"),
                    ("bind", "bot-2", "two@x", "https://sub.example/two"),
                    ("bulk_set_expiry", ["bot-id", "bot-2"]),
                ],
            )
        finally:
            KEYS.pop("bot-2")

    def test_install_twice_does_not_double_wrap(self):
        wrapped = self.cls.delete_user
        self.assertFalse(self.shim.install(self.cls))
        self.assertIs(self.cls.delete_user, wrapped)
        api = self.cls()
        self.run_(api.delete_user("bot-id"))
        self.assertEqual([c[0] for c in api.calls], ["bind", "delete_user"])

    def test_missing_methods_are_skipped(self):
        self.assertFalse(hasattr(self.cls, "enable_user"))

    def test_class_without_bind_is_left_alone(self):
        class Bare:
            async def delete_user(self, client_id):
                return True

        orig = Bare.delete_user
        self.assertFalse(self.shim.install(Bare))
        self.assertIs(Bare.delete_user, orig)


class RuntimeInstallsShim(unittest.TestCase):
    def test_runtime_calls_install_on_import(self):
        src = (ROOT / "panels/remnawave_runtime.py").read_text(encoding="utf-8")
        self.assertIn("from panels.remnawave_bind import install", src)
        self.assertIn("_install_bind_shim()", src)


if __name__ == "__main__":
    unittest.main()
