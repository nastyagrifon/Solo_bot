#!/usr/bin/env python3
"""Фоновые циклы подтягивают правки настроек админа без входящих апдейтов.

Правки *_CONFIG доезжали до процесса только из middleware апдейтов и из
зависимостей API. Цикл уведомлений в своём процессе или у «тихого» лидера
периодики так и удалял просроченные ключи со старым DELETE_KEY_ENABLED до
перезапуска. Проверка двумя способами: поведение опроса (модуль грузится
через importlib на заглушках, без базы и Redis) и разбор исходников (AST) —
опрос заведён у лидера, в процессах циклов и в задачах пула.

Запуск из корня проекта: ``venv/bin/python -m unittest tests.test_loops_runtime_sync -v``
"""

import ast
import asyncio
import importlib.util
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
REMOTE = {"version": 0.0, "configs": {}}


def _load_runtime_sync():
    async def cache_get(key):
        return REMOTE

    async def cache_set(*_a, **_k):
        return True

    redis_cache = types.ModuleType("core.redis_cache")
    redis_cache.cache_get = cache_get
    redis_cache.cache_set = cache_set
    redis_cache.cache_key = lambda prefix, *parts: ":".join((prefix, *map(str, parts)))
    models = types.ModuleType("database.models")
    models.Setting = type("Setting", (), {})
    settings_cache = types.ModuleType("database.settings_cache")
    settings_cache.settings_cache = object()
    cache_config = types.ModuleType("settings.cache_config")
    cache_config.RUNTIME_CONFIG_SYNC_PULL_INTERVAL_SEC = 0.0
    cache_config.RUNTIME_CONFIG_SYNC_TTL_SEC = 60
    logger = types.ModuleType("logger")
    logger.logger = types.SimpleNamespace(warning=lambda *a, **k: None)
    stubs = {
        "core.redis_cache": redis_cache,
        "database.models": models,
        "database.settings_cache": settings_cache,
        "settings.cache_config": cache_config,
        "logger": logger,
    }
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("runtime_sync_under_test", ROOT / "core/settings/runtime_sync.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _tree(rel):
    return ast.parse((ROOT / rel).read_text(encoding="utf-8"))


def _func(tree, name):
    return next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def _names(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


class BackgroundLoopPullsAdminChanges(unittest.TestCase):
    def test_loop_applies_change_without_updates(self):
        rs = _load_runtime_sync()
        self.assertTrue(hasattr(rs, "runtime_config_sync_loop"), "нет фонового опроса настроек")
        config = {"DELETE_KEY_ENABLED": True}
        rs.register_runtime_config("NOTIFICATIONS_CONFIG", config)
        REMOTE["version"] = 10**10
        REMOTE["configs"] = {"NOTIFICATIONS_CONFIG": {"DELETE_KEY_ENABLED": False}}

        async def run():
            task = asyncio.create_task(rs.runtime_config_sync_loop())
            await asyncio.sleep(0.05)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        asyncio.run(run())
        self.assertIs(config["DELETE_KEY_ENABLED"], False)

    def test_loop_survives_redis_error(self):
        rs = _load_runtime_sync()
        calls = []

        async def broken(force=False):
            calls.append(force)
            raise RuntimeError("redis down")

        rs.maybe_sync_runtime_configs = broken
        rs._BACKGROUND_SYNC_INTERVAL_SEC = 0.01

        async def run():
            task = asyncio.create_task(rs.runtime_config_sync_loop())
            await asyncio.sleep(0.05)
            self.assertFalse(task.done(), "опрос умер на первой ошибке")
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        asyncio.run(run())
        self.assertGreater(len(calls), 1)


class WiredIntoEveryBackgroundPath(unittest.TestCase):
    def test_leader_registers_sync_loop(self):
        tree = _tree("core/tasks/registry.py")
        loop_tasks = next(
            n.value for n in ast.walk(tree)
            if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "LOOP_TASKS" for t in n.targets)
        )
        self.assertIn("runtime_config_sync_loop", _names(loop_tasks))

    def test_process_loop_runs_sync(self):
        fn = _func(_tree("core/tasks/periodic_manager.py"), "_run_process_loop_task_async")
        self.assertIn("runtime_config_sync_loop", _names(fn))

    def test_process_pool_cron_job_syncs_first(self):
        tree = _tree("core/tasks/cron_tasks.py")
        src = ast.unparse(tree)
        run = _func(tree, "run_in_own_loop")
        called = {getattr(c.func, "id", "") for c in ast.walk(run) if isinstance(c, ast.Call)}
        called |= _names(run)
        reachable = [_func(tree, n) for n in called if f"def {n}(" in src]
        self.assertTrue(
            any("maybe_sync_runtime_configs" in _names(f) for f in [run, *reachable]),
            "задача пула не подтягивает правки настроек",
        )


if __name__ == "__main__":
    unittest.main()
