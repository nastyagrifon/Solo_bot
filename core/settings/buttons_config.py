from sqlalchemy.ext.asyncio import AsyncSession

from ..defaults import DEFAULT_BUTTONS_CONFIG
from .runtime_sync import load_setting, register_runtime_config, update_setting


def _normalize(config: dict[str, bool]) -> None:
    """Убирает снятую кнопку и досыпает новые, которых может не быть в дефолтах."""
    config.pop("TOGGLE_CLIENT_BUTTON_ENABLE", None)
    config.setdefault("ANDROID_TV_BUTTON_ENABLE", False)
    config.setdefault("COUPON_BUTTON_ENABLE", True)


BUTTONS_CONFIG: dict[str, bool] = DEFAULT_BUTTONS_CONFIG.copy()
_normalize(BUTTONS_CONFIG)
register_runtime_config("BUTTONS_CONFIG", BUTTONS_CONFIG)
_DESCRIPTION = "Конфигурация кнопок бота"


async def load_buttons_config(session: AsyncSession) -> None:
    await load_setting(
        session,
        "BUTTONS_CONFIG",
        BUTTONS_CONFIG,
        DEFAULT_BUTTONS_CONFIG,
        _DESCRIPTION,
        normalize=_normalize,
    )


async def update_buttons_config(session: AsyncSession, new_values: dict[str, bool]) -> None:
    await update_setting(
        session,
        "BUTTONS_CONFIG",
        BUTTONS_CONFIG,
        new_values,
        DEFAULT_BUTTONS_CONFIG,
        _DESCRIPTION,
        normalize=_normalize,
    )
