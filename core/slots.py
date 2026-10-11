"""Слоты A/B (issue #95): два процесса ядра по очереди на одном боте.

Готовность: /slot/ready отвечает 503, пока диспетчер не стартовал (модули и маршруты подключены,
периодика запущена или ждёт лидерства), потом 200 — по нему переключатель отдаёт процессу трафик.

Вебхук общий на оба слота. Ядро при старте ставит его с drop_pending_updates=True (выбрасывает
накопившиеся апдейты), при остановке снимает — старый слот оставлял бота глухим. В режиме слотов
(задан SLOT_PORT) ядро вебхук не трогает: его держит шлюз обновлений и пересылает апдейты в
активный слот.
"""

import os
import sys

from pathlib import Path

import aiohttp

from aiohttp import web

from logger import logger


_ready = False

#: Корень процесса: каталог, в котором лежит это ядро.
PROCESS_ROOT = str(Path(__file__).resolve().parent.parent)


def _looks_like_bot_root(path: str) -> bool:
    """Похож ли каталог на второй корень бота: рядом `bot.py` и пакет `api`."""
    try:
        base = Path(path).resolve()
    except (OSError, ValueError):
        return False
    return (base / "bot.py").is_file() and (base / "api").is_dir()


def guard_import_path(root: str = PROCESS_ROOT) -> list[str]:
    """Держит корень процесса первым в `sys.path` и убирает чужие копии дерева.

    Зачем: рядом со слотом живёт прежний дом бота с таким же деревом пакетов.
    Модулю достаточно сделать `sys.path.insert(0, ...)` на него — и импорт
    пойдёт из чужой копии, причём молча: поведение станет старым, а в журнале
    не будет ни одной ошибки. Так 10.10.2026 процесс и утащил 87 модулей
    пакета `api` из дома, из-за чего правки в `api/` на прод не доезжали.

    Возвращает список убранных путей — пусто, когда всё в порядке.
    """
    root = str(Path(root).resolve())
    ubrano = []
    ostalos = []
    for path in sys.path:
        if path and path != root and _looks_like_bot_root(path):
            ubrano.append(path)
            continue
        ostalos.append(path)
    sys.path[:] = [root] + [p for p in ostalos if p != root]
    if ubrano:
        logger.warning(
            "[Slots] В sys.path нашлась чужая копия дерева бота, убрана: %s. "
            "Корень процесса: %s", ", ".join(ubrano), root,
        )
    return ubrano


def mark_ready() -> None:
    global _ready
    _ready = True


def is_ready() -> bool:
    return _ready


async def _api_up() -> bool:
    """Встроенный API стартует в том же процессе уже после диспетчера и ~18 с держит цикл событий:
    пока он сам не ответил, отдавать слоту трафик рано."""
    from settings import config

    if not getattr(config, "API_ENABLE", False):
        return True
    try:
        async with aiohttp.ClientSession() as http:
            url = f"http://127.0.0.1:{config.API_PORT}/api/health"
            async with http.get(url, timeout=aiohttp.ClientTimeout(total=2)) as resp:
                return resp.status == 200
    except Exception:  # noqa: BLE001 — API ещё не поднялся
        return False


async def slot_ready(_request: web.Request) -> web.Response:
    ready = _ready and await _api_up()
    return web.json_response({"ready": ready}, status=200 if ready else 503)


def guard_webhook() -> None:
    """В режиме слотов вебхуком владеет шлюз обновлений: ядро не снимает и не ставит его.

    Патч на классе, а не на объекте: закрытое ядро зовёт set_webhook/delete_webhook у своего
    экземпляра Bot. Все методы API идут через Bot.__call__ — там и отсекаем.
    """
    if not os.environ.get("SLOT_PORT"):
        return
    from aiogram import Bot
    from aiogram.methods import DeleteWebhook, SetWebhook

    if getattr(Bot.__call__, "_slots_guard", False):
        return
    call = Bot.__call__

    async def guarded(self, method, request_timeout=None):
        if isinstance(method, (DeleteWebhook, SetWebhook)):
            logger.info("[Slots] {} пропущен: вебхуком владеет шлюз обновлений", type(method).__name__)
            return True
        return await call(self, method, request_timeout)

    guarded._slots_guard = True
    Bot.__call__ = guarded
