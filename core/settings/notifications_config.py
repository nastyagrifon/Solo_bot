from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..defaults import DEFAULT_NOTIFICATIONS_CONFIG
from .runtime_sync import load_setting, register_runtime_config, update_setting


NOTIFICATIONS_CONFIG: dict[str, Any] = DEFAULT_NOTIFICATIONS_CONFIG.copy()
register_runtime_config("NOTIFICATIONS_CONFIG", NOTIFICATIONS_CONFIG)
_DESCRIPTION = "Конфигурация уведомлений"


async def load_notifications_config(session: AsyncSession) -> None:
    await load_setting(
        session,
        "NOTIFICATIONS_CONFIG",
        NOTIFICATIONS_CONFIG,
        DEFAULT_NOTIFICATIONS_CONFIG,
        _DESCRIPTION,
    )


async def update_notifications_config(session: AsyncSession, new_values: dict[str, Any]) -> None:
    await update_setting(
        session,
        "NOTIFICATIONS_CONFIG",
        NOTIFICATIONS_CONFIG,
        new_values,
        DEFAULT_NOTIFICATIONS_CONFIG,
        _DESCRIPTION,
    )
