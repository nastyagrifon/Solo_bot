from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..defaults import DEFAULT_MONEY_CONFIG
from .runtime_sync import load_setting, register_runtime_config, update_setting


MONEY_CONFIG: dict[str, Any] = DEFAULT_MONEY_CONFIG.copy()
register_runtime_config("MONEY_CONFIG", MONEY_CONFIG)
_DESCRIPTION = "Конфигурация валютных настроек"


def get_currency_mode() -> tuple[str, bool]:
    mode_cfg = MONEY_CONFIG.get("CURRENCY_MODE", "RUB")
    raw = str(mode_cfg or "RUB").upper()

    if raw not in ("RUB", "USD", "RUB+USD", "RUB+USD_ONE_SCREEN"):
        raw = "RUB"

    one_screen = raw == "RUB+USD_ONE_SCREEN"
    if raw in ("RUB+USD", "RUB+USD_ONE_SCREEN"):
        base_mode = "RUB+USD"
    else:
        base_mode = raw

    return base_mode, one_screen


async def load_money_config(session: AsyncSession) -> None:
    await load_setting(session, "MONEY_CONFIG", MONEY_CONFIG, DEFAULT_MONEY_CONFIG, _DESCRIPTION)


async def update_money_config(session: AsyncSession, new_values: dict[str, Any]) -> None:
    await update_setting(session, "MONEY_CONFIG", MONEY_CONFIG, new_values, DEFAULT_MONEY_CONFIG, _DESCRIPTION)
