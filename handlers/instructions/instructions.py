import os

from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.bootstrap import MODES_CONFIG
from database import get_subscription_link
from handlers.keys.utils import build_key_callback, happ_import_url, owned_key_record
from handlers.utils import build_support_button, edit_or_send_message
from hooks.processors import process_remnawave_webapp_override
from services.clusters import is_full_remnawave_cluster
from settings.buttons import (
    BACK,
    CONNECT_MACOS_BUTTON,
    CONNECT_WINDOWS_BUTTON,
    DOWNLOAD_MACOS_BUTTON,
    DOWNLOAD_PC_BUTTON,
    MAIN_MENU,
    PC_MACOS,
    PC_PC,
    TV_CONTINUE,
)
from settings.config import (
    CONNECT_MACOS,
    CONNECT_WINDOWS,
    DOWNLOAD_MACOS,
    DOWNLOAD_PC,
    HAPP_CRYPTOLINK,
    REMNAWAVE_WEBAPP,
    WEBHOOK_HOST,
)
from settings.texts import (
    CHOOSE_DEVICE_TEXT,
    CONNECT_TV_TEXT,
    INSTRUCTIONS,
    INSTRUCTION_MACOS,
    INSTRUCTION_PC,
    KEY_MESSAGE,
    ROUTER_MESSAGE,
    SUBSCRIPTION_DETAILS_TEXT,
)


router = Router()


@router.callback_query(F.data == "instructions")
@router.message(F.text == "/instructions")
async def send_instructions(callback_query_or_message: CallbackQuery | Message):
    instructions_message = INSTRUCTIONS
    image_path = os.path.join("img", "instructions.jpg")

    builder = InlineKeyboardBuilder()
    support_btn = await build_support_button()
    if support_btn:
        builder.row(support_btn)
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    if isinstance(callback_query_or_message, CallbackQuery):
        target_message = callback_query_or_message.message
    else:
        target_message = callback_query_or_message

    await edit_or_send_message(
        target_message=target_message,
        text=instructions_message,
        reply_markup=builder.as_markup(),
        media_path=image_path,
    )


@router.callback_query(F.data.startswith("connect_pc|"), flags={"popup": True})
async def process_connect_pc(callback_query: CallbackQuery, session: Any):
    owned = await owned_key_record(callback_query, session)
    if not owned:
        return
    key_name, record = owned
    key_link = await get_subscription_link(session, key_name)
    if not key_link:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
        await edit_or_send_message(
            target_message=callback_query.message,
            text="❌ <b>Ключ не найден. Проверьте имя ключа.</b> 🔍",
            reply_markup=builder.as_markup(),
            media_path=None,
        )
        return

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=PC_PC, callback_data=build_key_callback("windows_menu", record.get("client_id"), key_name)
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=PC_MACOS, callback_data=build_key_callback("macos_menu", record.get("client_id"), key_name)
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=BACK, callback_data=build_key_callback("connect_device", record.get("client_id"), key_name)
        )
    )

    await edit_or_send_message(
        target_message=callback_query.message,
        text=CHOOSE_DEVICE_TEXT,
        reply_markup=builder.as_markup(),
        media_path=None,
    )


@router.callback_query(F.data.startswith(("windows_menu|", "macos_menu|")), flags={"popup": True})
async def process_pc_menu(callback_query: CallbackQuery, session: Any):
    if callback_query.data.startswith("windows_menu|"):
        instruction, download_text, download_url, connect_text, connect_prefix = (
            INSTRUCTION_PC, DOWNLOAD_PC_BUTTON, DOWNLOAD_PC, CONNECT_WINDOWS_BUTTON, CONNECT_WINDOWS,
        )
    else:
        instruction, download_text, download_url, connect_text, connect_prefix = (
            INSTRUCTION_MACOS, DOWNLOAD_MACOS_BUTTON, DOWNLOAD_MACOS, CONNECT_MACOS_BUTTON, CONNECT_MACOS,
        )
    owned = await owned_key_record(callback_query, session)
    if not owned:
        return
    key_name, record = owned
    key_link = await get_subscription_link(session, key_name)
    if not key_link:
        await callback_query.message.answer("❌ Ошибка: ключ не найден.")
        return

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=download_text, url=download_url))
    builder.row(InlineKeyboardButton(text=connect_text, url=happ_import_url(key_link, connect_prefix, WEBHOOK_HOST)))
    support_btn = await build_support_button()
    if support_btn:
        builder.row(support_btn)
    builder.row(
        InlineKeyboardButton(
            text=BACK, callback_data=build_key_callback("connect_pc", record.get("client_id"), key_name)
        )
    )

    await edit_or_send_message(
        target_message=callback_query.message,
        text=f"{KEY_MESSAGE.format(key_link)}{instruction}",
        reply_markup=builder.as_markup(),
        media_path=None,
    )


@router.callback_query(F.data.startswith("connect_tv|"), flags={"popup": True})
async def process_connect_tv(callback_query: CallbackQuery, session: Any):
    owned = await owned_key_record(callback_query, session)
    if not owned:
        return
    key_name, record = owned
    final_link = None
    is_full_remnawave = False
    use_webapp = False
    is_remnawave_webapp = False

    if record:
        server_name = record.get("server_id")
        final_link = record.get("key") or record.get("remnawave_link")

        if server_name:
            is_full_remnawave = await is_full_remnawave_cluster(server_name, session)

        remnawave_webapp_enabled = bool(MODES_CONFIG.get("REMNAWAVE_WEBAPP_ENABLED", REMNAWAVE_WEBAPP))
        happ_cryptolink_enabled = bool(MODES_CONFIG.get("HAPP_CRYPTOLINK_ENABLED", HAPP_CRYPTOLINK))

        use_webapp = remnawave_webapp_enabled
        if is_full_remnawave and final_link and remnawave_webapp_enabled and not happ_cryptolink_enabled:
            use_webapp = await process_remnawave_webapp_override(
                remnawave_webapp=remnawave_webapp_enabled,
                final_link=final_link,
                session=session,
            )

        is_remnawave_webapp = bool(is_full_remnawave and final_link and use_webapp and not happ_cryptolink_enabled)

    if not final_link:
        await callback_query.answer("❌ Ссылка подписки не найдена", show_alert=True)
        return

    back_callback = (
        build_key_callback("view_key", record.get("client_id"), key_name)
        if is_remnawave_webapp
        else build_key_callback("connect_device", record.get("client_id"), key_name)
    )

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=TV_CONTINUE, callback_data=build_key_callback("continue_tv", record.get("client_id"), key_name)
        )
    )
    builder.row(InlineKeyboardButton(text=BACK, callback_data=back_callback))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    text = CONNECT_TV_TEXT.format(subscription_link=final_link)

    await edit_or_send_message(
        target_message=callback_query.message,
        text=text,
        reply_markup=builder.as_markup(),
        media_path=None,
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("continue_tv|"), flags={"popup": True})
async def process_continue_tv(callback_query: CallbackQuery, session: Any):
    owned = await owned_key_record(callback_query, session)
    if not owned:
        return
    key_name, record = owned
    key_link = await get_subscription_link(session, key_name)
    message_text = SUBSCRIPTION_DETAILS_TEXT.format(subscription_link=key_link)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=BACK, callback_data=build_key_callback("connect_tv", record.get("client_id"), key_name)
        )
    )
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text=message_text,
        reply_markup=builder.as_markup(),
        media_path=None,
    )


@router.callback_query(F.data.startswith("connect_router|"), flags={"popup": True})
async def process_connect_router(callback_query: CallbackQuery, session: Any):
    owned = await owned_key_record(callback_query, session)
    if not owned:
        return
    key_name, record = owned
    key_link = await get_subscription_link(session, key_name)
    if not key_link:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
        await edit_or_send_message(
            target_message=callback_query.message,
            text="❌ Ключ не найден.",
            reply_markup=builder.as_markup(),
            media_path=None,
        )
        return

    message_text = ROUTER_MESSAGE.format(subscription_link=key_link)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=BACK, callback_data=build_key_callback("view_key", record.get("client_id"), key_name))
    )

    await edit_or_send_message(
        target_message=callback_query.message,
        text=message_text,
        reply_markup=builder.as_markup(),
        media_path=None,
    )
