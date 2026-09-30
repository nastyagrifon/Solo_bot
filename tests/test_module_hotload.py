"""Сквозная перезагрузка модуля настоящим менеджером: новый код без рестарта, старое снято.

Тестовый модуль создаётся во временной папке и подключается как ``modules.zz_hot``.
"""

import asyncio
import importlib
import os
import sys
import tempfile
import textwrap
import unittest

from pathlib import Path

from aiohttp.test_utils import make_mocked_request


MODULE_SRC = '''
import asyncio

from aiogram import Router
from aiohttp import web

from core import module_runtime as rt
from hooks.hooks import register_hook

VERSION = "{version}"
router = Router(name="zz_hot")
STATE = {{"ticks": 0}}


@register_hook("zz_hot_ping")
async def ping(**kwargs):
    return VERSION


async def webhook(request):
    return web.json_response({{"version": VERSION}})


def get_webhook_data():
    return {{"path": "/zz_hot/webhook", "handler": webhook}}


async def _tick():
    while True:
        STATE["ticks"] += 1
        await asyncio.sleep(0.01)


async def _mw(handler, event, data):
    data.setdefault("seen", []).append(VERSION)
    return await handler(event, data)


rt.add_middleware(_mw, observer="message", module="zz_hot")
rt.on_unload(lambda: CLEANED.append(VERSION), module="zz_hot")
CLEANED = __import__("builtins").__dict__.setdefault("_zz_hot_cleaned", [])


def start_background():
    rt.spawn(_tick(), module="zz_hot")
'''


class HotReloadTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.pkg = root / "modules" / "zz_hot"
        cls.pkg.mkdir(parents=True)
        (cls.pkg / "__init__.py").write_text("")
        (cls.pkg / "router.py").write_text(MODULE_SRC.format(version="v1"))
        os.environ["MODULES_STATE_FILE"] = str(root / "storage" / "modules_state.json")
        # ``modules`` из временной папки впереди боевого, если он уже импортирован
        for name in [m for m in sys.modules if m == "modules" or m.startswith("modules.")]:
            sys.modules.pop(name)
        sys.path.insert(0, str(root))
        import utils.modules_manager as mm

        importlib.reload(mm)
        cls.mm = mm

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(cls.tmp.name)
        for name in [m for m in sys.modules if m.startswith("modules.zz_hot")]:
            sys.modules.pop(name)
        cls.tmp.cleanup()

    async def test_reload_swaps_code_and_releases_resources(self):
        from core import module_runtime as rt
        from hooks.hooks import run_hooks
        from utils.modules_loader import modules_hub

        manager = self.mm.ModulesManager()
        proxy = rt.web_proxy("/zz_hot/webhook")

        await manager.start("zz_hot")
        mod_v1 = sys.modules["modules.zz_hot.router"]
        mod_v1.start_background()
        await asyncio.sleep(0.05)
        self.assertIn(manager.registry["zz_hot"].router, modules_hub.sub_routers)
        self.assertEqual((await proxy(make_mocked_request("POST", "/zz_hot/webhook"))).text, '{"version": "v1"}')
        self.assertEqual(await run_hooks("zz_hot_ping", require_enabled=False), ["v1"])
        self.assertEqual(rt.snapshot()["zz_hot"]["tasks"], 1)

        # правим код модуля на диске и перезапускаем
        (self.pkg / "router.py").write_text(MODULE_SRC.format(version="v2"))
        await manager.restart("zz_hot")
        ticks_after_stop = mod_v1.STATE["ticks"]
        await asyncio.sleep(0.05)
        self.assertEqual(mod_v1.STATE["ticks"], ticks_after_stop, "старая задача должна быть отменена")

        self.assertEqual((await proxy(make_mocked_request("POST", "/zz_hot/webhook"))).text, '{"version": "v2"}')
        self.assertEqual(await run_hooks("zz_hot_ping", require_enabled=False), ["v2"])
        self.assertIn("v1", __import__("builtins")._zz_hot_cleaned, "откат v1 выполнен")
        mw_chain = rt.ModuleMiddlewareProxy("message")
        data = {}

        async def final(event, d):
            return d

        await mw_chain(final, object(), data)
        self.assertEqual(data["seen"], ["v2"], "в цепочке только middleware новой версии")
        self.assertEqual(sum(r is manager.registry["zz_hot"].router for r in modules_hub.sub_routers), 1)

        await manager.stop("zz_hot")
        self.assertNotIn("zz_hot", rt.snapshot())
        self.assertEqual((await proxy(make_mocked_request("POST", "/zz_hot/webhook"))).status, 404)
        self.assertEqual(await run_hooks("zz_hot_ping", require_enabled=False), [])


if __name__ == "__main__":
    unittest.main()
