from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import BUTTONS_CONFIG, update_buttons_config
from filters.admin import IsAdminFilter

from ..panel.headers import menu_text, quote
from ..panel.keyboard import AdminPanelCallback
from .keyboard import BUTTON_TITLES, build_settings_buttons_kb
from .toggle import toggle_setting


router = Router(name="admin_settings_buttons")
router.callback_query.filter(IsAdminFilter())


async def load_button_settings() -> dict[str, bool]:
    config = BUTTONS_CONFIG or {}
    return {k: bool(config.get(k, False)) for k in BUTTON_TITLES.keys()}


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_buttons"))
async def open_settings_buttons_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    buttons_state = await load_button_settings()
    text = menu_text(
        "Кнопки",
        "Что клиент видит в меню бота.",
        quote("Нажмите на кнопку, чтобы показать или спрятать её."),
    )
    await callback.message.edit_text(text=text, reply_markup=build_settings_buttons_kb(buttons_state))
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_button_toggle"), flags={"popup": True})
async def toggle_button_setting(
    callback: CallbackQuery,
    callback_data: AdminPanelCallback,
    session: AsyncSession,
) -> None:
    config = await toggle_setting(
        callback, session, callback_data.page, BUTTON_TITLES, BUTTONS_CONFIG, update_buttons_config
    )
    if config is None:
        return
    buttons_state = {k: bool(config.get(k, False)) for k in BUTTON_TITLES.keys()}
    await callback.message.edit_reply_markup(reply_markup=build_settings_buttons_kb(buttons_state))
    await callback.answer(menu_text("Кнопки", "Настройка обновлена"))
