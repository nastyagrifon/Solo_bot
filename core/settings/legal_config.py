from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..defaults import DEFAULT_LEGAL_CONFIG
from .runtime_sync import load_setting, register_runtime_config, update_setting


LEGAL_CONFIG: dict[str, Any] = DEFAULT_LEGAL_CONFIG.copy()
register_runtime_config("LEGAL_CONFIG", LEGAL_CONFIG)
_DESCRIPTION = "Правовые документы бота"

LEGAL_DOC_KEYS: tuple[str, ...] = ("LEGAL_PRIVACY_URL", "LEGAL_TERMS_URL", "LEGAL_OFFER_URL")


def is_legal_enabled() -> bool:
    """Раздел показывается только когда включён и есть хотя бы одна ссылка."""
    if not bool(LEGAL_CONFIG.get("LEGAL_DOCS_ENABLED", False)):
        return False
    return any(str(LEGAL_CONFIG.get(key) or "").strip() for key in LEGAL_DOC_KEYS)


def legal_doc_url(key: str) -> str:
    return str(LEGAL_CONFIG.get(key) or "").strip()


async def load_legal_config(session: AsyncSession) -> None:
    await load_setting(session, "LEGAL_CONFIG", LEGAL_CONFIG, DEFAULT_LEGAL_CONFIG, _DESCRIPTION)


async def update_legal_config(session: AsyncSession, new_values: dict[str, Any]) -> None:
    await update_setting(session, "LEGAL_CONFIG", LEGAL_CONFIG, new_values, DEFAULT_LEGAL_CONFIG, _DESCRIPTION)
