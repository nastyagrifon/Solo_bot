from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from handlers.admin.panel.keyboard import AdminPanelCallback, nav_row
from settings.buttons import BACK
from utils.modules_manager import manager


def build_modules_kb(page: int, total_pages: int, items: list[tuple[str, str | None]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    row_buf = []
    for name, _ in items:
        label = name if manager.is_enabled(name) else f"{name} (off)"
        row_buf.append(
            InlineKeyboardButton(
                text=label,
                callback_data=AdminPanelCallback(action=f"module__{name}", page=page).pack(),
            )
        )
        builder.row(*row_buf)
        row_buf = []

    nav = nav_row(
        page,
        total_pages,
        lambda p: AdminPanelCallback(action="modules", page=p).pack(),
        prev_text=BACK,
        next_text="Вперед ➡️",
    )
    if nav:
        builder.row(*nav)

    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminPanelCallback(action="admin", page=1).pack(),
        )
    )

    return builder.as_markup()


def build_module_menu_kb(name: str, page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    enabled = manager.is_enabled(name)

    if enabled:
        builder.button(
            text="🔁 Перезапустить",
            callback_data=AdminPanelCallback(action=f"module_restart__{name}", page=page).pack(),
        )
        builder.button(
            text="🛑 Остановить",
            callback_data=AdminPanelCallback(action=f"module_stop__{name}", page=page).pack(),
        )
    else:
        builder.button(
            text="▶️ Запустить",
            callback_data=AdminPanelCallback(action=f"module_start__{name}", page=page).pack(),
        )

    builder.button(
        text="🔄 Обновить",
        callback_data=AdminPanelCallback(action=f"module_update__{name}", page=page).pack(),
    )

    builder.button(
        text="⬆️ settings.py",
        callback_data=AdminPanelCallback(action="module_upload_settings", page=page).pack(),
    )
    builder.button(
        text="⬆️ texts.py",
        callback_data=AdminPanelCallback(action="module_upload_texts", page=page).pack(),
    )

    builder.adjust(2)
    builder.row(
        InlineKeyboardButton(
            text="⬅️ К списку",
            callback_data=AdminPanelCallback(action="modules", page=page).pack(),
        )
    )
    return builder.as_markup()
