from sqlalchemy.ext.asyncio import AsyncSession

from settings.config import YOOKASSA_SBP_ONLY

from ..defaults import DEFAULT_PAYMENTS_CONFIG
from .runtime_sync import get_setting, put_setting, register_runtime_config, update_setting


PAYMENTS_CONFIG: dict[str, bool] = DEFAULT_PAYMENTS_CONFIG.copy()
register_runtime_config("PAYMENTS_CONFIG", PAYMENTS_CONFIG)
_DESCRIPTION = "Конфигурация платёжных провайдеров"


async def load_payments_config(session: AsyncSession) -> None:
    from .yookassa_autopay_config import migrate_missing_autopay_flag

    setting = await get_setting(session, "PAYMENTS_CONFIG")
    stored = await migrate_missing_autopay_flag(session, {} if setting is None else setting.value or {})
    if setting is None:
        payments_config = DEFAULT_PAYMENTS_CONFIG.copy()
        payments_config.update(stored)
        if YOOKASSA_SBP_ONLY and payments_config.get("YOOKASSA"):
            payments_config.update(YOOKASSA=False, YOOKASSA_SBP=True)
    else:
        if "YOOKASSA_SBP" not in stored and YOOKASSA_SBP_ONLY and stored.get("YOOKASSA"):
            stored = {**stored, "YOOKASSA": False, "YOOKASSA_SBP": True}
        payments_config = DEFAULT_PAYMENTS_CONFIG.copy()
        payments_config.update(stored)
    put_setting(session, setting, "PAYMENTS_CONFIG", payments_config, _DESCRIPTION)

    PAYMENTS_CONFIG.clear()
    PAYMENTS_CONFIG.update(payments_config)
    await session.flush()


async def update_payments_config(session: AsyncSession, new_values: dict[str, bool]) -> None:
    await update_setting(session, "PAYMENTS_CONFIG", PAYMENTS_CONFIG, new_values, DEFAULT_PAYMENTS_CONFIG, _DESCRIPTION)
