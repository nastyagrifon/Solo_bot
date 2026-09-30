from sqlalchemy.ext.asyncio import AsyncSession

from ..defaults import DEFAULT_MENU_LAYOUT
from .runtime_sync import apply_setting, get_setting, put_setting, register_runtime_config


MENU_LAYOUT: dict[str, list[list[str]]] = {menu: [row.copy() for row in rows] for menu, rows in DEFAULT_MENU_LAYOUT.items()}
register_runtime_config("MENU_LAYOUT", MENU_LAYOUT)
_DESCRIPTION = "Порядок кнопок в меню бота"


def _normalize(stored: object) -> dict[str, list[list[str]]]:
    """Приводит сохранённую раскладку к виду «меню → ряды → идентификаторы»."""
    layout = {menu: [row.copy() for row in rows] for menu, rows in DEFAULT_MENU_LAYOUT.items()}
    if not isinstance(stored, dict):
        return layout

    for menu, rows in stored.items():
        if menu not in layout or not isinstance(rows, list):
            continue
        clean = [
            [str(button) for button in row if isinstance(button, str) and button]
            for row in rows
            if isinstance(row, list)
        ]
        layout[menu] = [row for row in clean if row]
    return layout


async def load_menu_layout(session: AsyncSession) -> None:
    # Новую строку создаём с сырыми дефолтами, существующую не переписываем.
    setting = await get_setting(session, "MENU_LAYOUT")
    if setting is None:
        put_setting(session, None, "MENU_LAYOUT", DEFAULT_MENU_LAYOUT, _DESCRIPTION)
        layout = _normalize(DEFAULT_MENU_LAYOUT)
    else:
        layout = _normalize(setting.value)

    MENU_LAYOUT.clear()
    MENU_LAYOUT.update(layout)
    await session.flush()


async def update_menu_layout(session: AsyncSession, new_layout: dict[str, list[list[str]]]) -> None:
    layout = _normalize(new_layout)
    put_setting(session, await get_setting(session, "MENU_LAYOUT"), "MENU_LAYOUT", layout, _DESCRIPTION)
    await session.commit()
    await apply_setting("MENU_LAYOUT", MENU_LAYOUT, layout)
