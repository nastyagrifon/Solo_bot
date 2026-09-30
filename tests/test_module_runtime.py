"""Среда модулей: учёт HTTP, middleware, задач и откатов, выгрузка.

Запуск из корня проекта: ``venv/bin/python -m unittest discover -s tests -v``
"""

import asyncio
import unittest

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from core import module_runtime as rt


class RuntimeTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        rt._modules.clear()

    def tearDown(self):
        rt._modules.clear()


class WebTests(RuntimeTestCase):
    async def test_proxy_picks_up_new_handler_after_reregister(self):
        async def v1(request):
            return web.json_response({"v": 1})

        async def v2(request):
            return web.json_response({"v": 2})

        proxy = rt.web_proxy("/hot/webhook")
        rt.register_web("/hot/webhook", v1, module="hot")
        r1 = await proxy(make_mocked_request("POST", "/hot/webhook"))
        self.assertEqual(r1.text, '{"v": 1}')

        await rt.unload("hot")
        r_gone = await proxy(make_mocked_request("POST", "/hot/webhook"))
        self.assertEqual(r_gone.status, 404)

        rt.register_web("hot/webhook/", v2, module="hot")  # путь нормализуется
        r2 = await proxy(make_mocked_request("POST", "/hot/webhook"))
        self.assertEqual(r2.text, '{"v": 2}')

    async def test_module_prefix_dispatch(self):
        async def handler(request):
            return web.Response(text="late")

        rt.register_web("/added-later", handler, module="late")
        req = make_mocked_request("POST", "/m/late/added-later", match_info={"module": "late", "tail": "added-later"})
        self.assertEqual((await rt.module_web_dispatch(req)).text, "late")
        req = make_mocked_request("POST", "/m/nope/x", match_info={"module": "nope", "tail": "x"})
        self.assertEqual((await rt.module_web_dispatch(req)).status, 404)


class MiddlewareTests(RuntimeTestCase):
    async def test_chain_order_and_removal(self):
        seen = []

        def mw(tag):
            async def call(handler, event, data):
                seen.append(tag)
                return await handler(event, data)

            return call

        rt.add_middleware(mw("a1"), observer="message", module="a")
        rt.add_middleware(mw("b1"), observer="message", module="b")
        rt.add_middleware(mw("upd"), observer="update", module="a")
        proxy = rt.ModuleMiddlewareProxy("message")

        async def final(event, data):
            seen.append("handler")
            return "ok"

        self.assertEqual(await proxy(final, object(), {}), "ok")
        self.assertEqual(seen, ["a1", "b1", "handler"])

        seen.clear()
        await rt.unload("a")
        await proxy(final, object(), {})
        self.assertEqual(seen, ["b1", "handler"])

    def test_bad_observer(self):
        with self.assertRaises(ValueError):
            rt.add_middleware(object(), observer="edited_message", module="x")


class TaskAndCleanupTests(RuntimeTestCase):
    async def test_unload_cancels_tasks_and_runs_cleanups_in_reverse(self):
        order = []
        started = asyncio.Event()

        async def forever():
            started.set()
            await asyncio.sleep(3600)

        task = rt.spawn(forever(), module="bg")
        await started.wait()
        rt.on_unload(lambda: order.append("first"), module="bg")

        async def async_cleanup():
            order.append("second")

        rt.on_unload(async_cleanup, module="bg")
        rt.on_unload(lambda: 1 / 0, module="bg")  # сломанный откат не мешает остальным

        stats = await rt.unload("bg")
        self.assertTrue(task.cancelled())
        self.assertEqual(order, ["second", "first"])
        self.assertEqual(stats["tasks"], 1)
        self.assertEqual(stats["cleanups"], 3)
        self.assertEqual(rt.snapshot(), {})

    async def test_unload_unknown_module_is_noop(self):
        self.assertEqual(await rt.unload("never"), {"web": 0, "middlewares": 0, "tasks": 0, "cleanups": 0})


class ModuleNameTests(RuntimeTestCase):
    def test_empty_module_name_rejected(self):
        with self.assertRaises(ValueError):
            rt.on_unload(lambda: None, module="")


if __name__ == "__main__":
    unittest.main()
