from sqlalchemy.ext.asyncio import AsyncSession

from ..defaults import DEFAULT_MODES_CONFIG
from .runtime_sync import load_setting, register_runtime_config, update_setting


MODES_CONFIG: dict[str, bool] = DEFAULT_MODES_CONFIG.copy()
register_runtime_config("MODES_CONFIG", MODES_CONFIG)
_DESCRIPTION = "Конфигурация режимов работы бота"


def resolve_protect_content() -> bool:
    return bool(MODES_CONFIG.get("PROTECT_CONTENT_ENABLED", False))


def apply_protect_content_to_bot() -> None:
    import sys

    bot_module = sys.modules.get("bot")
    if bot_module is None:
        return
    bot = getattr(bot_module, "bot", None)
    if bot is None or getattr(bot, "default", None) is None:
        return
    try:
        bot.default.protect_content = resolve_protect_content()
    except Exception:
        pass


async def load_modes_config(session: AsyncSession) -> None:
    await load_setting(
        session,
        "MODES_CONFIG",
        MODES_CONFIG,
        DEFAULT_MODES_CONFIG,
        _DESCRIPTION,
        on_apply=apply_protect_content_to_bot,
    )


async def update_modes_config(session: AsyncSession, new_values: dict[str, bool]) -> None:
    await update_setting(
        session,
        "MODES_CONFIG",
        MODES_CONFIG,
        new_values,
        DEFAULT_MODES_CONFIG,
        _DESCRIPTION,
        on_apply=apply_protect_content_to_bot,
    )
