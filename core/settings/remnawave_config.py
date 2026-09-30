from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..defaults import DEFAULT_REMNAWAVE_CONFIG
from .runtime_sync import apply_setting, get_setting, load_setting, put_setting, register_runtime_config


REMNAWAVE_CONFIG: dict[str, Any] = DEFAULT_REMNAWAVE_CONFIG.copy()
REMNAWAVE_SETTING_KEY = "REMNAWAVE_CONFIG"
register_runtime_config(REMNAWAVE_SETTING_KEY, REMNAWAVE_CONFIG)
_DESCRIPTION = "Конфигурация интеграции с Remnawave (мониторинг + ротация хостов)"


async def load_remnawave_config(session: AsyncSession) -> None:
    await load_setting(session, REMNAWAVE_SETTING_KEY, REMNAWAVE_CONFIG, DEFAULT_REMNAWAVE_CONFIG, _DESCRIPTION)


async def update_remnawave_config(session: AsyncSession, new_values: dict[str, Any]) -> None:
    # Здесь, в отличие от соседей, в БД пишется уже слитый с дефолтами конфиг.
    setting = await get_setting(session, REMNAWAVE_SETTING_KEY)
    merged = DEFAULT_REMNAWAVE_CONFIG.copy()
    merged.update(new_values)
    put_setting(session, setting, REMNAWAVE_SETTING_KEY, merged, _DESCRIPTION)
    await session.commit()
    await apply_setting(REMNAWAVE_SETTING_KEY, REMNAWAVE_CONFIG, merged)


def is_node_health_enabled() -> bool:
    return bool(REMNAWAVE_CONFIG.get("NODE_HEALTH_ENABLED", False))


def is_host_rotation_enabled() -> bool:
    return bool(REMNAWAVE_CONFIG.get("HOST_ROTATION_ENABLED", False))


def is_host_auto_disable_enabled() -> bool:
    return bool(REMNAWAVE_CONFIG.get("HOST_AUTO_DISABLE_ON_NODE_DOWN", False))


def get_host_auto_disabled() -> set[str]:
    raw = REMNAWAVE_CONFIG.get("HOST_AUTO_DISABLED") or []
    if not isinstance(raw, list):
        return set()
    return {str(uuid) for uuid in raw if uuid}


def get_host_rotation_allowed() -> set[str]:
    raw = REMNAWAVE_CONFIG.get("HOST_ROTATION_ALLOWED") or []
    if not isinstance(raw, list):
        return set()
    return {str(uuid) for uuid in raw if uuid}


def get_node_health_allowed() -> set[str]:
    raw = REMNAWAVE_CONFIG.get("NODE_HEALTH_ALLOWED") or []
    if not isinstance(raw, list):
        return set()
    return {str(uuid) for uuid in raw if uuid}
