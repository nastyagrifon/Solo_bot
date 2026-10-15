from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Setting


async def get_setting(session: AsyncSession, key: str, default: Any = None) -> Any:
    stmt = select(Setting).where(Setting.key == key)
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()
    if setting is None or setting.value is None:
        return default
    return setting.value


async def set_setting(
    session: AsyncSession,
    key: str,
    value: Any,
    description: str | None = None,
) -> Setting:
    stmt = select(Setting).where(Setting.key == key)
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        setting = Setting(key=key, value=value, description=description)
        session.add(setting)
    else:
        setting.value = value
        if description is not None:
            setting.description = description

    await session.flush()

    # Записать в базу недостаточно: живые процессы (бот, API, поддержка) читают
    # настройки из своего реестра в памяти, а он синхронизируется через снимок
    # в Redis по полю version. Без публикации version не меняется,
    # maybe_sync_runtime_configs считает кэш свежим, и правка не действует до
    # перезапуска.
    #
    # 15.10.2026 на этом потерялись деньги: CASHBACK=50 записали в базу, бот
    # 38 минут начислял прежние 30%, двое клиентов получили не ту сумму.
    #
    # Импорт отложенный: core.settings.runtime_sync тянет database на уровне
    # модуля, и верхний импорт даёт циклическую зависимость.
    # Незарегистрированный ключ publish_runtime_config пропустит сам, а сбой
    # публикации не должен ронять запись - она уже в базе, хуже кэша нет.
    if isinstance(value, dict):
        try:
            from core.settings.runtime_sync import publish_runtime_config

            await publish_runtime_config(key, value)
        except Exception as exc:
            from logger import logger

            logger.warning(f"[Settings] {key} записан, но снимок не обновлён: {exc}")

    return setting
