from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..defaults import DEFAULT_WEB_CONFIG
from .runtime_sync import load_setting, register_runtime_config, update_setting


WEB_CONFIG: dict[str, Any] = DEFAULT_WEB_CONFIG.copy()
WEB_SETTING_KEY = "WEB_CONFIG"
register_runtime_config(WEB_SETTING_KEY, WEB_CONFIG)
_DESCRIPTION = "Конфигурация веб-сайта"


async def load_web_config(session: AsyncSession) -> None:
    await load_setting(session, WEB_SETTING_KEY, WEB_CONFIG, DEFAULT_WEB_CONFIG, _DESCRIPTION)


async def update_web_config(session: AsyncSession, new_values: dict[str, Any]) -> None:
    await update_setting(session, WEB_SETTING_KEY, WEB_CONFIG, new_values, DEFAULT_WEB_CONFIG, _DESCRIPTION)


def get_site_url() -> str:
    """Возвращает SITE_URL из WEB_CONFIG, или из config.py как fallback."""
    url = str(WEB_CONFIG.get("SITE_URL") or "").strip()
    if url:
        return url.rstrip("/")
    from settings.config import SITE_URL

    return SITE_URL.rstrip("/") if SITE_URL else ""


def is_web_enabled() -> bool:
    return bool(WEB_CONFIG.get("WEB_ENABLED", False))


def is_email_binding_enabled() -> bool:
    return bool(WEB_CONFIG.get("EMAIL_BINDING_ENABLED", False))


def get_site_mode() -> str:
    """Режим сайта: full — с лендингом, cabinet_only — только кабинет, webapp_only — только веб-апп."""
    return str(WEB_CONFIG.get("SITE_MODE", "full")).strip() or "full"


def is_cabinet_only() -> bool:
    """Сайт работает только как кабинет: витрина скрыта."""
    return get_site_mode() == "cabinet_only"


def is_webapp_only() -> bool:
    """Сайт живёт только внутри Telegram: браузерная витрина и вход по почте не нужны."""
    return get_site_mode() == "webapp_only"


def is_web_open_in_browser() -> bool:
    if is_webapp_only():
        return False
    return bool(WEB_CONFIG.get("WEB_OPEN_IN_BROWSER", False))


def get_web_node_status_interval_min() -> int:
    try:
        return max(1, int(WEB_CONFIG.get("WEB_NODE_STATUS_INTERVAL_MIN") or 1))
    except (TypeError, ValueError):
        return 1
