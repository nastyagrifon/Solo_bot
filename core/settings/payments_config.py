from sqlalchemy.ext.asyncio import AsyncSession

from ..defaults import DEFAULT_PAYMENTS_CONFIG
from .runtime_sync import load_setting, register_runtime_config, update_setting


PAYMENTS_CONFIG: dict[str, bool] = DEFAULT_PAYMENTS_CONFIG.copy()
register_runtime_config("PAYMENTS_CONFIG", PAYMENTS_CONFIG)
_DESCRIPTION = "Конфигурация платёжных провайдеров"


async def load_payments_config(session: AsyncSession) -> None:
    await load_setting(session, "PAYMENTS_CONFIG", PAYMENTS_CONFIG, DEFAULT_PAYMENTS_CONFIG, _DESCRIPTION)


async def update_payments_config(session: AsyncSession, new_values: dict[str, bool]) -> None:
    await update_setting(session, "PAYMENTS_CONFIG", PAYMENTS_CONFIG, new_values, DEFAULT_PAYMENTS_CONFIG, _DESCRIPTION)
