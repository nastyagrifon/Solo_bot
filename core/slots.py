"""Слоты A/B (issue #95): два процесса ядра по очереди на одном боте.

Готовность: /slot/ready отвечает 503, пока диспетчер не стартовал (модули и маршруты подключены,
периодика запущена или ждёт лидерства), потом 200 — по нему переключатель отдаёт процессу трафик.

Вебхук общий на оба слота. Ядро при старте ставит его с drop_pending_updates=True (выбрасывает
накопившиеся апдейты), при остановке снимает — старый слот оставлял бота глухим. В режиме слотов
(задан SLOT_PORT) ядро вебхук не трогает: его держит шлюз обновлений и пересылает апдейты в
активный слот.
"""

import os

from aiohttp import web

from logger import logger


_ready = False


def mark_ready() -> None:
    global _ready
    _ready = True


def is_ready() -> bool:
    return _ready


async def slot_ready(_request: web.Request) -> web.Response:
    return web.json_response({"ready": _ready}, status=200 if _ready else 503)


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
