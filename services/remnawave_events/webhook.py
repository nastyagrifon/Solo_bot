"""HTTP-приёмник событий панели."""

import asyncio
import json

from aiohttp import web

from hooks.hooks import run_hooks
from logger import logger

from .envelope import extract_data, extract_event, signature_valid

SIGNATURE_HEADER = "X-Remnawave-Signature"
HOOK_NAME = "remnawave_event"

# Держим ссылки на фоновые задачи, иначе сборщик мусора может снять их до конца.
_background: set[asyncio.Task] = set()


def make_handler(secret: str):
    """Обработчик с зашитым секретом. Панели отвечаем 200 всегда, кроме неверной
    подписи: на любой другой код она повторяет доставку, а чинить всё равно у нас.
    """

    async def remnawave_webhook(request: web.Request) -> web.Response:
        raw = await request.read()
        if not signature_valid(raw, request.headers.get(SIGNATURE_HEADER), secret):
            logger.warning("[RW_EVENTS] неверная подпись от {}", request.remote)
            return web.Response(status=403)

        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            logger.warning("[RW_EVENTS] тело не JSON, {} байт", len(raw))
            return web.Response(status=200)
        if not isinstance(payload, dict):
            return web.Response(status=200)

        event = extract_event(payload)
        if not event:
            logger.info("[RW_EVENTS] событие без имени, пропущено")
            return web.Response(status=200)

        task = asyncio.create_task(run_hooks(HOOK_NAME, event=event, data=extract_data(payload), payload=payload))
        _background.add(task)
        task.add_done_callback(_background.discard)
        return web.Response(status=200)

    return remnawave_webhook
