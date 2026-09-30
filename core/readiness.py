"""Готовность процесса ядра: по ней переключатель слотов A/B решает, можно ли отдавать ему трафик.

Готов = диспетчер стартовал (модули и маршруты подключены, периодика запущена или ждёт лидерства).
До этого /slot/ready отвечает 503, и reverse proxy не шлёт в процесс запросы.
"""

from aiohttp import web


_ready = False


def mark_ready() -> None:
    global _ready
    _ready = True


def is_ready() -> bool:
    return _ready


async def slot_ready(_request: web.Request) -> web.Response:
    return web.json_response({"ready": _ready}, status=200 if _ready else 503)
