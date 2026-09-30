"""Среда модулей: всё, что модуль заводит у бота, — на учёте, чтобы снять при выгрузке.

Роутер и хуки модуля менеджер снимает и так. Остального ядро раньше не видело,
и оно переживало перезагрузку модуля старым кодом:

- HTTP-маршрут модуля регистрируется в aiohttp один раз при старте, после старта
  роутер aiohttp заморожен — «перезагруженный» модуль продолжал отвечать старым
  обработчиком;
- middleware, поставленные модулем на диспетчер, оставались старыми объектами;
- фоновые задачи модуля не останавливались — после перезагрузки их становилось две;
- подмены функций ядра (monkey-patch) не откатывались.

Здесь это становится учётом по модулю, а менеджер модулей при выгрузке зовёт
``unload(name)``. Модуль пользуется так::

    from core import module_runtime as rt

    rt.register_web("/example/webhook", handler, module="example")  # путь не меняется между перезагрузками
    rt.add_middleware(MyMiddleware(), observer="callback_query", module="example")
    rt.spawn(poll_forever(), module="example")                      # отменится при выгрузке
    rt.on_unload(lambda: setattr(core_mod, "fn", original), module="example")  # откат подмены

Имя модуля передаётся явно параметром ``module``.
"""

from __future__ import annotations

import asyncio
import inspect

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiohttp import web

from logger import logger


OBSERVERS = ("update", "message", "callback_query")
MODULE_WEB_PREFIX = "/m"


class _ModuleResources:
    """Что заведено одним модулем."""

    def __init__(self) -> None:
        self.web: dict[str, Callable[[web.Request], Awaitable[web.StreamResponse]]] = {}
        self.middlewares: dict[str, list[BaseMiddleware]] = {o: [] for o in OBSERVERS}
        self.tasks: set[asyncio.Task] = set()
        self.cleanups: list[Callable[[], Any]] = []


_modules: dict[str, _ModuleResources] = {}


def _resources(module: str) -> _ModuleResources:
    if not module:
        raise ValueError("module_runtime: передайте module=<имя модуля>")
    return _modules.setdefault(module, _ModuleResources())


def _normalize_path(path: str) -> str:
    return "/" + (path or "").strip().strip("/")


# ─────────────────────────── HTTP ───────────────────────────


def register_web(path: str, handler, *, module: str) -> None:
    """Обработчик HTTP модуля. Ищется при каждом запросе — перезагрузка подхватывает новый код.

    Пути, известные на старте, ядро регистрирует в aiohttp как постоянные посредники;
    появившиеся позже доступны по ``/m/<модуль>/<путь>``.
    """
    _resources(module).web[_normalize_path(path)] = handler


def resolve_web(path: str):
    """Текущий обработчик для пути или None."""
    path = _normalize_path(path)
    for res in _modules.values():
        handler = res.web.get(path)
        if handler is not None:
            return handler
    return None


def web_proxy(path: str):
    """Постоянный обработчик aiohttp для пути модуля: на каждый запрос берёт актуальный."""
    path = _normalize_path(path)

    async def proxy(request: web.Request) -> web.StreamResponse:
        handler = resolve_web(path)
        if handler is None:
            return web.Response(status=404, text="module is not loaded")
        return await handler(request)

    proxy.__name__ = f"module_web_proxy{path.replace('/', '_')}"
    return proxy


async def module_web_dispatch(request: web.Request) -> web.StreamResponse:
    """``/m/{module}/{tail}`` — маршрут для путей, появившихся после старта."""
    module = request.match_info.get("module", "")
    tail = _normalize_path(request.match_info.get("tail", ""))
    res = _modules.get(module)
    handler = res.web.get(tail) if res else None
    if handler is None:
        return web.Response(status=404, text="not found")
    return await handler(request)


# ───────────────────────── middleware ─────────────────────────


def add_middleware(middleware: BaseMiddleware, *, observer: str = "update", module: str) -> None:
    """Middleware модуля. Работает через посредника ядра, снимается при выгрузке."""
    if observer not in OBSERVERS:
        raise ValueError(f"module_runtime: observer должен быть одним из {OBSERVERS}")
    _resources(module).middlewares[observer].append(middleware)


class ModuleMiddlewareProxy(BaseMiddleware):
    """Один на тип событий; прогоняет событие через middleware всех загруженных модулей."""

    def __init__(self, observer: str) -> None:
        self.observer = observer

    async def __call__(self, handler, event, data):
        chain = [mw for res in list(_modules.values()) for mw in res.middlewares[self.observer]]
        call = handler
        for mw in reversed(chain):
            call = _bind(mw, call)
        return await call(event, data)


def _bind(mw, nxt):
    async def step(event, data):
        return await mw(nxt, event, data)

    return step


def install_middleware_proxies(dispatcher) -> None:
    """Поставить посредников на диспетчер. Один раз при старте."""
    if getattr(dispatcher, "_module_runtime_proxies", False):
        return
    for observer in OBSERVERS:
        getattr(dispatcher, observer).outer_middleware(ModuleMiddlewareProxy(observer))
    dispatcher._module_runtime_proxies = True


# ─────────────────────── задачи и откат ───────────────────────


def spawn(coro, *, module: str) -> asyncio.Task:
    """Фоновая задача модуля. Отменяется при выгрузке модуля."""
    res = _resources(module)
    task = asyncio.get_running_loop().create_task(coro, name=f"module:{module}")
    res.tasks.add(task)
    task.add_done_callback(res.tasks.discard)
    return task


def on_unload(callback: Callable[[], Any], *, module: str) -> None:
    """Функция отката при выгрузке (например, вернуть подменённую функцию ядра)."""
    _resources(module).cleanups.append(callback)


# ─────────────────────────── выгрузка ───────────────────────────


def _stats(res: _ModuleResources) -> dict[str, int]:
    return {
        "web": len(res.web),
        "middlewares": sum(len(v) for v in res.middlewares.values()),
        "tasks": len([t for t in res.tasks if not t.done()]),
        "cleanups": len(res.cleanups),
    }


async def unload(module: str) -> dict[str, int]:
    """Снять всё, что заведено модулем. Ошибки отката не мешают выгрузке остального."""
    res = _modules.pop(module, None)
    if res is None:
        return _stats(_ModuleResources())
    stats = _stats(res)

    tasks = [t for t in res.tasks if not t.done()]
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    for cb in reversed(res.cleanups):
        try:
            out = cb()
            if inspect.isawaitable(out):
                await out
        except Exception as e:
            logger.error("[ModuleRuntime] {}: ошибка отката: {}", module, e)

    logger.info("[ModuleRuntime] {} выгружен: {}", module, stats)
    return stats


def snapshot() -> dict[str, dict[str, int]]:
    """Что заведено каждым модулем — для админки и проверок."""
    return {name: _stats(res) for name, res in _modules.items()}
