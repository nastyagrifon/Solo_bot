"""Слоты A/B (issue #95): два процесса ядра по очереди на одном боте.

Готовность: /slot/ready отвечает 503, пока диспетчер не стартовал (модули и маршруты подключены,
периодика запущена или ждёт лидерства), потом 200 — по нему переключатель отдаёт процессу трафик.

Вебхук общий на оба слота. Запуск ядра снимает его при старте и при остановке, поэтому новый слот
на время своего старта оставлял бота глухим, а старый при остановке снимал вебхук уже нового.
В режиме слотов (задан SLOT_PORT) снятие — пустая операция, установка не выбрасывает накопившиеся
апдейты; жизненным циклом вебхука управляет переключатель.
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


def guard_webhook(bot) -> None:
    if not os.environ.get("SLOT_PORT"):
        return
    set_webhook = bot.set_webhook

    async def keep_webhook(*_args, **kwargs):
        logger.info("[Slots] delete_webhook пропущен: вебхук общий для слотов ({})", kwargs or "")
        return True

    async def set_keeping_pending(*args, **kwargs):
        kwargs["drop_pending_updates"] = False
        return await set_webhook(*args, **kwargs)

    bot.delete_webhook = keep_webhook
    bot.set_webhook = set_keeping_pending
