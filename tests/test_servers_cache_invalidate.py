#!/usr/bin/env python3
"""Правка сервера в админке сбрасывает кеш серверов и адрес панели Remnawave.

Удаление сервера, включение/выключение и смена лимита ключей писали в базу,
но кеш servers:* не сбрасывали: выключенный мёртвый сервер ещё до минуты
получал новые ключи. А кеш адреса панели remna_server:* (5 минут) не входил
в шаблоны сброса вовсе, и смена api_url держала старую панель.

Две проверки: сброс кеша вызывается на заглушке Redis (database/servers.py
грузится через importlib, без базы и Redis), а то, что обработчики админки
зовут сброс после каждой записи в Server, — разбором исходника (AST).

Запуск из корня проекта: ``venv/bin/python -m unittest tests.test_servers_cache_invalidate -v``
"""

import ast
import asyncio
import importlib.util
import pathlib
import sys
import types
import unittest

from fnmatch import fnmatchcase

ROOT = pathlib.Path(__file__).resolve().parent.parent
HANDLERS = ROOT / "handlers/admin/servers/server_manage.py"


def _load_servers(deleted):
    async def cache_delete_pattern(pattern):
        deleted.append(pattern)
        return 0

    async def _noop(*_a, **_k):
        return None

    redis_cache = types.ModuleType("core.redis_cache")
    redis_cache.cache_delete_pattern = cache_delete_pattern
    redis_cache.cache_get = _noop
    redis_cache.cache_set = _noop
    redis_cache.cache_key = lambda prefix, *parts: ":".join((prefix, *map(str, parts)))
    purge = types.ModuleType("database.cache_purge")
    purge.defer_purge_patterns = lambda session, *p: False
    purge.is_purge_pending = lambda session, key: False
    models = types.ModuleType("database.models")
    for name in ("Key", "Server", "ServerSpecialgroup", "ServerSubgroup", "Tariff"):
        setattr(models, name, type(name, (), {}))
    cache_config = types.ModuleType("settings.cache_config")
    cache_config.SERVERS_CACHE_TTL_SEC = 60
    logger = types.ModuleType("logger")
    logger.logger = types.SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None)
    stubs = {
        "core.redis_cache": redis_cache,
        "database.cache_purge": purge,
        "database.models": models,
        "settings.cache_config": cache_config,
        "logger": logger,
    }
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("servers_under_test", ROOT / "database/servers.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _writes_server(call):
    """execute(update(Server)...) / execute(stmt_delete), где stmt_delete — delete(Server)."""
    src = ast.unparse(call)
    return "update(Server)" in src or "stmt_delete" in src


class PurgePatterns(unittest.TestCase):
    def test_remna_server_cache_is_purged(self):
        deleted = []
        servers = _load_servers(deleted)
        asyncio.run(servers._invalidate_servers_cache(None))
        # Ключ кеша адреса панели: cache_key("remna_server", server_ref, fallback) в panels/remnawave_runtime.py.
        for key in ("servers:1", "remna_server:de-1:0", "remna_server:cluster:1"):
            self.assertTrue(any(fnmatchcase(key, p) for p in deleted), f"{key} не сбрасывается: {deleted}")


class AdminHandlersInvalidate(unittest.TestCase):
    def test_every_server_write_is_followed_by_invalidation(self):
        tree = ast.parse(HANDLERS.read_text(encoding="utf-8"))
        checked = 0
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)):
            blocks = (
                getattr(n, field)
                for n in ast.walk(fn)
                for field in ("body", "orelse", "finalbody")
                if isinstance(getattr(n, field, None), list)
            )
            for body in blocks:
                for i, stmt in enumerate(body):
                    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Await)):
                        continue
                    call = stmt.value.value
                    if not (isinstance(call, ast.Call) and ast.unparse(call.func) == "session.execute"):
                        continue
                    if not _writes_server(call):
                        continue
                    checked += 1
                    nxt = ast.unparse(body[i + 1]) if i + 1 < len(body) else ""
                    self.assertIn(
                        "_invalidate_servers_cache(session)", nxt,
                        f"{fn.name}: запись в Server строкой {stmt.lineno} без сброса кеша",
                    )
        self.assertEqual(checked, 4, "ожидались удаление (2 ветки), вкл/выкл и лимит")


if __name__ == "__main__":
    unittest.main()
