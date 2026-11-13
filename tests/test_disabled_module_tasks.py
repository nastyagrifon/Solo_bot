#!/usr/bin/env python3
"""Фоновые циклы выключенного модуля не запускаются и снимаются с остановкой.

discover_module_loop_tasks не смотрел, выключен ли модуль: после перезапуска
цикл выключенного админом модуля снова слал сообщения. А остановка модуля
снимала роутер, хуки и задачи из module_runtime, но не его цикл в менеджере
периодики — тот работал до перезапуска.

Модули грузятся через importlib на заглушках (logger, settings.config,
utils.*), без базы, Redis и запуска бота; менеджер периодики настоящий,
блокировка лидера подменена.

Запуск из корня проекта: ``venv/bin/python -m unittest tests.test_disabled_module_tasks -v``
"""

import asyncio
import importlib.util
import pathlib
import sys
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TASKS_SRC = '''
async def tick(bot, sessionmaker):
    import asyncio
    while True:
        await asyncio.sleep(3600)

def get_loop_tasks():
    return {"tick": tick}
'''


class _Manager:
    def __init__(self, disabled):
        self.disabled = set(disabled)

    def should_autostart(self, name):
        return name not in self.disabled


def _stub_common():
    logger = types.ModuleType("logger")
    noop = lambda *a, **k: None  # noqa: E731
    logger.logger = types.SimpleNamespace(
        warning=noop, info=noop, error=noop, debug=noop, opt=lambda **k: types.SimpleNamespace(error=noop)
    )
    core_features = types.ModuleType("utils.core_features")
    core_features.is_core_replaced_module = lambda name: False
    config = types.ModuleType("settings.config")
    config.PROCESS_POOL_SIZE = 0
    config.API_TOKEN = ""
    utils = types.ModuleType("utils")
    utils.__path__ = []
    settings = types.ModuleType("settings")
    settings.__path__ = []
    core = types.ModuleType("core")
    core.__path__ = [str(ROOT / "core")]
    return {
        "logger": logger,
        "utils": utils,
        "utils.core_features": core_features,
        "settings": settings,
        "settings.config": config,
        "core": core,
    }


def _load(rel, name, extra):
    stubs = {**_stub_common(), **extra}
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, saved


def _restore(saved):
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


class DiscoverySkipsDisabled(unittest.TestCase):
    def test_disabled_module_loop_not_discovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "modules"
            for name in ("alive", "off"):
                (base / name).mkdir(parents=True)
                (base / name / "__init__.py").write_text("")
                (base / name / "tasks.py").write_text(TASKS_SRC)
            (base / "__init__.py").write_text("")
            mm = types.ModuleType("utils.modules_manager")
            mm.manager = _Manager({"off"})
            mod, saved = _load("core/tasks/module_tasks.py", "module_tasks_under_test", {"utils.modules_manager": mm})
            sys.path.insert(0, tmp)
            try:
                for k in [k for k in sys.modules if k == "modules" or k.startswith("modules.")]:
                    sys.modules.pop(k)
                mod.MODULES_DIR = base
                ids = [task_id for task_id, _ in mod.discover_module_loop_tasks()]
            finally:
                sys.path.remove(tmp)
                for k in [k for k in sys.modules if k == "modules" or k.startswith("modules.")]:
                    sys.modules.pop(k)
                _restore(saved)
        self.assertEqual(ids, ["module:alive:tick"])


class StopCancelsModuleLoop(unittest.TestCase):
    def test_module_unload_cancels_its_periodic_loop(self):
        pm, saved = _load("core/tasks/periodic_manager.py", "periodic_manager_under_test", {})
        try:
            from core import module_runtime

            async def run():
                started = asyncio.Event()

                async def tick(bot, sessionmaker):
                    started.set()
                    await asyncio.sleep(3600)

                async def other(bot, sessionmaker):
                    await asyncio.sleep(3600)

                manager = pm.PeriodicTaskManager()
                manager._acquire_process_lock = lambda: True
                manager._release_process_lock = lambda: None
                manager.register_loop_task("module:off:tick", tick)
                manager.register_loop_task("core_loop", other)
                await manager.start(None, None)
                try:
                    await asyncio.wait_for(started.wait(), 2)
                    await module_runtime.unload("off")
                    await asyncio.sleep(0)
                    module_done = manager._running_tasks["module:off:tick"].done()
                    core_done = manager._running_tasks["core_loop"].done()
                finally:
                    await manager.stop()
                return module_done, core_done

            module_done, core_done = asyncio.run(run())
        finally:
            for k in [k for k in sys.modules if k.startswith("core.")]:
                sys.modules.pop(k)
            _restore(saved)
        self.assertTrue(module_done, "цикл модуля пережил его остановку")
        self.assertFalse(core_done, "остановка модуля сняла чужой цикл")


if __name__ == "__main__":
    unittest.main()
