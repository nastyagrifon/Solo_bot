#!/usr/bin/env python3
"""Задача пула не оставляет за собой клиент Redis и движок базы своего цикла.

run_in_own_loop запускает каждую задачу пула через asyncio.run в долгоживущем
воркере. Клиент Redis хранится по (pid, id(loop)) и после смерти цикла не
закрывался, а движок базы, созданный на прогон, бросался без dispose:
задача раз в 10 минут копила около 144 клиентов в сутки на воркер.

cron_tasks.py и настоящий core/redis_cache.py грузятся через importlib на
заглушках (database, logger, settings.config, core.slow_log); соединений с
Redis и базой нет — клиент и движок поддельные.

Запуск из корня проекта: ``venv/bin/python -m unittest tests.test_cron_loop_clients -v``
"""

import importlib.util
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


class _Engine:
    created = []

    def __init__(self):
        self.disposed = False
        _Engine.created.append(self)

    async def dispose(self):
        self.disposed = True


class _Client:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


def _load():
    noop = lambda *a, **k: None  # noqa: E731
    logger = types.ModuleType("logger")
    logger.logger = types.SimpleNamespace(info=noop, error=noop, warning=noop, debug=noop)
    config = types.ModuleType("settings.config")
    config.REDIS_URL = ""
    settings = types.ModuleType("settings")
    settings.__path__ = []
    slow_log = types.ModuleType("core.slow_log")
    slow_log.watch_redis = lambda client, _name: client
    core = types.ModuleType("core")
    core.__path__ = []
    db = types.ModuleType("database.db")
    db.engine = _Engine()

    def reset_async_db_engine():
        db.engine = _Engine()

    db.reset_async_db_engine = reset_async_db_engine
    database = types.ModuleType("database")
    database.__path__ = []
    database.db = db
    database.async_session_maker = None
    database.cancel_expired_pending_payments = None
    stubs = {
        "logger": logger,
        "settings": settings,
        "settings.config": config,
        "core": core,
        "core.slow_log": slow_log,
        "database": database,
        "database.db": db,
    }
    sys.modules.update(stubs)
    loaded = {}
    for name, rel in (("core.redis_cache", "core/redis_cache.py"), ("cron_tasks_under_test", "core/tasks/cron_tasks.py")):
        spec = importlib.util.spec_from_file_location(name, ROOT / rel)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        loaded[name] = mod
    return loaded["core.redis_cache"], loaded["cron_tasks_under_test"]


class CronJobReleasesLoopResources(unittest.TestCase):
    def test_repeated_jobs_do_not_leak_clients_or_engines(self):
        redis_cache, cron = _load()
        clients = []

        async def job():
            client = _Client()
            clients.append(client)
            # То же, что делает _get_redis при первом обращении задачи к Redis.
            redis_cache._REDIS_CLIENTS[redis_cache._client_key()] = client

        _Engine.created.clear()
        for _ in range(3):
            cron.run_in_own_loop(job)

        self.assertEqual(redis_cache._REDIS_CLIENTS, {}, "клиенты Redis мёртвых циклов остались в кеше")
        self.assertTrue(all(c.closed for c in clients), "клиенты Redis не закрыты")
        self.assertEqual(len(_Engine.created), 3)
        self.assertTrue(all(e.disposed for e in _Engine.created), "движки прогонов не закрыты")

    def test_failed_job_still_releases(self):
        redis_cache, cron = _load()

        async def job():
            redis_cache._REDIS_CLIENTS[redis_cache._client_key()] = _Client()
            raise RuntimeError("boom")

        _Engine.created.clear()
        with self.assertRaises(RuntimeError):
            cron.run_in_own_loop(job)
        self.assertEqual(redis_cache._REDIS_CLIENTS, {})
        self.assertTrue(_Engine.created[0].disposed)


if __name__ == "__main__":
    unittest.main()
