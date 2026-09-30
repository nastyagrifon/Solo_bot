from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..defaults import DEFAULT_MANAGEMENT_CONFIG
from .runtime_sync import load_setting, register_runtime_config, update_setting


MANAGEMENT_CONFIG: dict[str, Any] = DEFAULT_MANAGEMENT_CONFIG.copy()
MANAGEMENT_SETTING_KEY = "MANAGEGENT_CONFIG"
register_runtime_config(MANAGEMENT_SETTING_KEY, MANAGEMENT_CONFIG)
_DESCRIPTION = "Конфигурация управления ботом"


async def load_management_config(session: AsyncSession) -> None:
    await load_setting(session, MANAGEMENT_SETTING_KEY, MANAGEMENT_CONFIG, DEFAULT_MANAGEMENT_CONFIG, _DESCRIPTION)


async def update_management_config(session: AsyncSession, new_values: dict[str, Any]) -> None:
    await update_setting(
        session, MANAGEMENT_SETTING_KEY, MANAGEMENT_CONFIG, new_values, DEFAULT_MANAGEMENT_CONFIG, _DESCRIPTION
    )
