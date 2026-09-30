from sqlalchemy.ext.asyncio import AsyncSession

from .runtime_sync import apply_setting, get_setting, put_setting, register_runtime_config


PROVIDERS_ORDER: dict[str, int] = {}
register_runtime_config("PROVIDERS_ORDER", PROVIDERS_ORDER)


async def load_providers_order(session: AsyncSession) -> None:
    # Строку не создаём: нет порядка — пустой словарь.
    setting = await get_setting(session, "PROVIDERS_ORDER")

    PROVIDERS_ORDER.clear()
    if setting and isinstance(setting.value, dict):
        PROVIDERS_ORDER.update({k: int(v) for k, v in setting.value.items()})
    await session.flush()


async def update_providers_order(session: AsyncSession, new_order: dict[str, int]) -> None:
    setting = await get_setting(session, "PROVIDERS_ORDER")
    put_setting(session, setting, "PROVIDERS_ORDER", new_order, "Порядок отображения платёжных провайдеров")
    await session.commit()
    await apply_setting("PROVIDERS_ORDER", PROVIDERS_ORDER, new_order)
