from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession


async def toggle_setting(
    callback: CallbackQuery,
    session: AsyncSession,
    index: int,
    titles: Mapping[str, Any],
    config: Mapping[str, Any] | None,
    save: Callable[[AsyncSession, dict], Awaitable[Any]],
    unknown: str = "Неизвестная настройка",
) -> dict | None:
    """Переворачивает флаг номер index (с единицы, по порядку titles) и сохраняет конфиг.

    Возвращает новый конфиг; None — номер вне списка, клиенту уже показан алерт.
    """
    keys = list(titles)
    if not 1 <= index <= len(keys):
        await callback.answer(unknown, show_alert=True)
        return None
    key = keys[index - 1]
    new_config = dict(config or {})
    new_config[key] = not bool(new_config.get(key, False))
    await save(session, new_config)
    return new_config
