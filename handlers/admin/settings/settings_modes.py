from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import MODES_CONFIG, update_modes_config
from filters.admin import IsAdminFilter

from ..panel.headers import menu_text, quote
from ..panel.keyboard import AdminPanelCallback
from .keyboard import MODES_TITLES, build_settings_modes_kb
from .toggle import toggle_setting


router = Router(name="admin_settings_modes")
router.callback_query.filter(IsAdminFilter())


async def load_modes_settings() -> dict[str, bool]:
    config = MODES_CONFIG or {}
    return {k: bool(config.get(k, False)) for k in MODES_TITLES.keys()}


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_modes"))
async def open_settings_modes_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    modes_state = await load_modes_settings()
    text = menu_text(
        "Режимы",
        "Как бот себя ведёт.",
        quote("Нажмите на режим, чтобы включить или выключить его."),
    )
    await callback.message.edit_text(text=text, reply_markup=build_settings_modes_kb(modes_state))
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_modes_toggle"), flags={"popup": True})
async def toggle_mode_setting(
    callback: CallbackQuery,
    callback_data: AdminPanelCallback,
    session: AsyncSession,
) -> None:
    config = await toggle_setting(
        callback, session, callback_data.page, MODES_TITLES,
        {k: bool((MODES_CONFIG or {}).get(k, False)) for k in MODES_TITLES.keys()}, update_modes_config,
    )
    if config is None:
        return
    modes_state = {k: bool(config.get(k, False)) for k in MODES_TITLES.keys()}
    await callback.message.edit_reply_markup(reply_markup=build_settings_modes_kb(modes_state))
    await callback.answer(menu_text("Режимы", "Настройка обновлена"))
