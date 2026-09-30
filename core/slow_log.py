"""Метки медленного: что ждёт дольше секунды и какие ошибки иначе проглатываются молча.

Кэш Redis и отправщик уведомлений глотают исключения, поэтому зависание выглядит
в логе как тишина. Здесь один порог и три точки съёма: команды Redis (вместе
с ожиданием соединения из пула), запросы в Telegram и задержка цикла событий.
"""

import asyncio
import time

from typing import Any

from aiogram.client.session.middlewares.base import BaseRequestMiddleware

from logger import logger


SLOW_SEC = 1.0


def warn_if_slow(what: str, started: float, error: BaseException | None = None) -> None:
    elapsed = time.monotonic() - started
    if error is not None:
        logger.warning(f"[Slow] {what}: {type(error).__name__} за {elapsed:.2f} с: {error}")
    elif elapsed >= SLOW_SEC:
        logger.warning(f"[Slow] {what}: {elapsed:.2f} с")


def watch_redis(client: Any, name: str) -> Any:
    """Все команды клиента идут через execute_command — там и меряем, и ловим ошибку."""
    execute = client.execute_command

    async def timed(*args: Any, **options: Any) -> Any:
        started = time.monotonic()
        try:
            result = await execute(*args, **options)
        except Exception as error:
            warn_if_slow(f"Redis {name} {args[0] if args else '?'}", started, error)
            raise
        warn_if_slow(f"Redis {name} {args[0] if args else '?'}", started)
        return result

    client.execute_command = timed
    return client


class SlowTelegramRequests(BaseRequestMiddleware):
    async def __call__(self, make_request, bot, method):
        started = time.monotonic()
        try:
            return await make_request(bot, method)
        finally:
            warn_if_slow(f"Telegram {type(method).__name__}", started)


async def loop_lag_loop(_bot, _sessionmaker, interval: float = 0.5) -> None:
    """Цикл событий один на все клики: если его кто-то держит, проснёмся позже срока."""
    while True:
        started = time.monotonic()
        await asyncio.sleep(interval)
        lag = time.monotonic() - started - interval
        if lag >= SLOW_SEC:
            logger.warning(f"[Slow] цикл событий занят: опоздание {lag:.2f} с")
