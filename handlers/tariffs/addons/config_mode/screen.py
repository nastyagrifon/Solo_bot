from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from core.settings.tariffs_config import TARIFFS_CONFIG
from logger import logger
from services.payments.currency_rates import format_for_user
from services.tariffs.pricing import calculate_config_price
from settings.buttons import (
    CONFIRM_ADDON_BUTTON_TEXT,
    DOWNGRADE_ADDON_BUTTON_TEXT,
)
from settings.texts import (
    DOWNGRADE_INLINE_WARNING_TEXT,
)

from ..utils import (
    add_option_rows,
    build_addons_screen_text,
    format_devices_label,
    format_traffic_label,
    is_not_downgrade,
    load_addons_screen_state,
    send_addons_screen,
)


router = Router()


async def render_addons_screen(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    loaded = await load_addons_screen_state(callback, state, session, pack=False)
    if loaded is None:
        return
    data, email, tariff, device_int_options, traffic_int_options = loaded

    current_devices = data.get("addon_current_device_limit")
    current_traffic_gb = data.get("addon_current_traffic_gb")
    original_price = int(data.get("addon_original_price") or 0)

    selected_devices = data.get("addon_selected_device_limit")
    selected_traffic_gb = data.get("addon_selected_traffic_gb")

    tariff_name = tariff.get("name") or "подписка"

    has_device_option = bool(device_int_options)
    has_traffic_option = bool(traffic_int_options)

    has_device_choice = len(device_int_options) > 1
    has_traffic_choice = len(traffic_int_options) > 1

    if selected_devices is None and has_device_option:
        if current_devices is not None and int(current_devices) in device_int_options:
            selected_devices = int(current_devices)
        elif device_int_options:
            selected_devices = device_int_options[0]

    if selected_traffic_gb is None and has_traffic_option:
        if current_traffic_gb is not None and int(current_traffic_gb) in traffic_int_options:
            selected_traffic_gb = int(current_traffic_gb)
        elif traffic_int_options:
            selected_traffic_gb = traffic_int_options[0]

    logger.debug(
        "[ADDONS] Limits before price: "
        f"current_devices={current_devices} current_traffic_gb={current_traffic_gb} "
        f"selected_devices={selected_devices} selected_traffic_gb={selected_traffic_gb} "
        f"original_price={original_price}"
    )

    await state.update_data(
        addon_selected_device_limit=selected_devices,
        addon_selected_traffic_gb=selected_traffic_gb,
    )

    current_devices_for_price = int(current_devices) if current_devices is not None and has_device_option else None
    current_traffic_for_price = (
        int(current_traffic_gb) if current_traffic_gb is not None and has_traffic_option else None
    )
    base_price_for_current = calculate_config_price(
        tariff=tariff,
        selected_device_limit=current_devices_for_price,
        selected_traffic_gb=current_traffic_for_price,
    )

    total_price = calculate_config_price(
        tariff=tariff,
        selected_device_limit=int(selected_devices) if selected_devices is not None and has_device_option else None,
        selected_traffic_gb=int(selected_traffic_gb)
        if selected_traffic_gb is not None and has_traffic_option
        else None,
    )
    extra_price = max(0, total_price - base_price_for_current)

    logger.debug(
        "[ADDONS] Calculated prices: "
        f"base_price_for_current={base_price_for_current} total_price={total_price} extra_price={extra_price} "
        f"has_device_option={has_device_option} has_traffic_option={has_traffic_option}"
    )

    tg_id = callback.from_user.id
    language_code = getattr(callback.from_user, "language_code", None)

    total_price_text = await format_for_user(session, tg_id, float(total_price), language_code)
    extra_price_text = await format_for_user(session, tg_id, float(extra_price), language_code)

    current_devices_label = format_devices_label(current_devices)
    current_traffic_label = format_traffic_label(current_traffic_gb)
    new_devices_label = format_devices_label(selected_devices)
    new_traffic_label = format_traffic_label(selected_traffic_gb)

    downgrade_warning = None
    devices_downgrade = False
    traffic_downgrade = False
    allow_downgrade = bool(TARIFFS_CONFIG.get("ALLOW_DOWNGRADE", True))

    if allow_downgrade:
        if has_device_choice and current_devices is not None and selected_devices is not None:
            devices_downgrade = not is_not_downgrade(current_devices, selected_devices)
        if has_traffic_choice and current_traffic_gb is not None and selected_traffic_gb is not None:
            traffic_downgrade = not is_not_downgrade(current_traffic_gb, selected_traffic_gb)
        if devices_downgrade or traffic_downgrade:
            new_limits_parts = []
            if has_device_choice:
                new_limits_parts.append(new_devices_label)
            if has_traffic_choice:
                new_limits_parts.append(new_traffic_label)
            new_limits_desc = ", ".join(new_limits_parts) if new_limits_parts else "выбранные параметры"
            downgrade_warning = DOWNGRADE_INLINE_WARNING_TEXT.format(
                total_price_text=total_price_text,
                new_limits_desc=new_limits_desc,
            )

    logger.debug(
        "[ADDONS] Downgrade flags: "
        f"devices_downgrade={devices_downgrade} traffic_downgrade={traffic_downgrade} "
        f"ALLOW_DOWNGRADE={allow_downgrade}"
    )

    text = build_addons_screen_text(
        tariff_name=tariff_name,
        current_devices_label=current_devices_label,
        current_traffic_label=current_traffic_label,
        new_devices_label=new_devices_label,
        new_traffic_label=new_traffic_label,
        has_device_choice=has_device_choice,
        has_traffic_choice=has_traffic_choice,
        total_price_text=total_price_text,
        extra_price_text=extra_price_text,
        downgrade_warning=downgrade_warning,
    )

    builder = InlineKeyboardBuilder()

    allowed_devices = (
        [v for v in device_int_options if allow_downgrade or is_not_downgrade(current_devices, v)]
        if has_device_choice
        else []
    )
    allowed_traffic = (
        [v for v in traffic_int_options if allow_downgrade or is_not_downgrade(current_traffic_gb, v)]
        if has_traffic_choice
        else []
    )

    add_option_rows(builder, email, allowed_devices, selected_devices, allowed_traffic, selected_traffic_gb)

    if allow_downgrade and (devices_downgrade or traffic_downgrade):
        builder.row(
            InlineKeyboardButton(
                text=DOWNGRADE_ADDON_BUTTON_TEXT,
                callback_data="key_addons_downgrade",
            )
        )
    else:
        builder.row(
            InlineKeyboardButton(
                text=CONFIRM_ADDON_BUTTON_TEXT.format(amount=extra_price_text),
                callback_data="key_addons_confirm",
            )
        )

    await send_addons_screen(callback, session, builder, text, data, email)
