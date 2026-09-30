from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import MODES_CONFIG
from core.settings.tariffs_config import TARIFFS_CONFIG
from logger import logger
from services.addons import (
    calc_pack_full_price_rub,
    get_pack_flags,
)
from services.payments.currency_rates import format_for_user
from services.tariffs.pricing import calculate_config_price
from settings.buttons import CONFIRM_ADDON_BUTTON_TEXT

from ..utils import (
    add_option_rows,
    build_addons_pack_screen_text,
    calc_remaining_ratio_seconds,
    format_devices_label,
    format_traffic_label,
    load_addons_screen_state,
    send_addons_screen,
)


router = Router()


async def render_addons_screen(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    loaded = await load_addons_screen_state(callback, state, session, pack=True)
    if loaded is None:
        return
    data, email, tariff, device_int_options, traffic_int_options = loaded

    current_devices = data.get("addon_current_device_limit")
    current_traffic_gb = data.get("addon_current_traffic_gb")
    selected_devices = data.get("addon_selected_device_limit")
    selected_traffic_gb = data.get("addon_selected_traffic_gb")
    expiry_time = data.get("addon_expiry_time")

    tariff_name = tariff.get("name") or "подписка"

    pack_devices, pack_traffic, pack_mode = get_pack_flags()

    has_device_option = pack_devices and bool(device_int_options)
    has_traffic_option = pack_traffic and bool(traffic_int_options)

    if has_device_option and current_devices is not None and int(current_devices) == 0:
        has_device_option = False
        selected_devices = None

    if has_traffic_option and current_traffic_gb is not None and int(current_traffic_gb) == 0:
        has_traffic_option = False
        selected_traffic_gb = None

    if not has_device_option:
        selected_devices = None
    if not has_traffic_option:
        selected_traffic_gb = None

    await state.update_data(
        addon_selected_device_limit=selected_devices,
        addon_selected_traffic_gb=selected_traffic_gb,
    )

    current_devices_for_price = int(current_devices) if current_devices is not None else None
    current_traffic_for_price = int(current_traffic_gb) if current_traffic_gb is not None else None

    base_price_for_current = calculate_config_price(
        tariff=tariff,
        selected_device_limit=current_devices_for_price,
        selected_traffic_gb=current_traffic_for_price,
    )
    try:
        base_price_for_current_int = int(base_price_for_current) if base_price_for_current is not None else 0
    except (TypeError, ValueError):
        base_price_for_current_int = 0

    recalc_enabled = bool(
        MODES_CONFIG.get(
            "KEY_ADDONS_RECALC_PRICE",
            TARIFFS_CONFIG.get("KEY_ADDONS_RECALC_PRICE", False),
        )
    )

    diff_full = calc_pack_full_price_rub(
        tariff=tariff,
        has_device_option=has_device_option,
        has_traffic_option=has_traffic_option,
        selected_devices=int(selected_devices) if selected_devices is not None else None,
        selected_traffic_gb=int(selected_traffic_gb) if selected_traffic_gb is not None else None,
    )

    if recalc_enabled:
        remaining_seconds, total_seconds = calc_remaining_ratio_seconds(expiry_time, tariff)
        extra_price = int((diff_full * remaining_seconds + total_seconds - 1) // total_seconds)
    else:
        extra_price = int(diff_full)

    logger.debug(
        "[ADDONS] PACK_MODE calculated prices: "
        f"base_price_for_current={base_price_for_current_int} diff_full={diff_full} "
        f"extra_price={extra_price} recalc_enabled={recalc_enabled} "
        f"has_device_option={has_device_option} has_traffic_option={has_traffic_option} pack_mode={pack_mode!r}"
    )

    tg_id = callback.from_user.id
    language_code = getattr(callback.from_user, "language_code", None)

    extra_price_text = await format_for_user(session, tg_id, float(extra_price), language_code)

    current_devices_label = format_devices_label(current_devices)
    current_traffic_label = format_traffic_label(current_traffic_gb)

    has_device_pack_selected = has_device_option and selected_devices is not None
    has_traffic_pack_selected = has_traffic_option and selected_traffic_gb is not None

    selected_devices_label = format_devices_label(selected_devices) if has_device_pack_selected else None
    selected_traffic_label = format_traffic_label(selected_traffic_gb) if has_traffic_pack_selected else None

    if has_device_pack_selected:
        current_devices_value = int(current_devices) if current_devices else 0
        selected_devices_value = int(selected_devices)
        total_devices_value = (
            0
            if current_devices_value <= 0 or selected_devices_value <= 0
            else current_devices_value + selected_devices_value
        )
        total_devices_label = format_devices_label(total_devices_value)
    else:
        total_devices_label = None

    if has_traffic_pack_selected:
        current_traffic_value = int(current_traffic_gb) if current_traffic_gb else 0
        selected_traffic_value = int(selected_traffic_gb)
        total_after_gb = (
            0
            if current_traffic_value <= 0 or selected_traffic_value <= 0
            else current_traffic_value + selected_traffic_value
        )
        total_traffic_label = format_traffic_label(total_after_gb)
    else:
        total_traffic_label = None

    text = build_addons_pack_screen_text(
        tariff_name=tariff_name,
        current_devices_label=current_devices_label,
        current_traffic_label=current_traffic_label if current_traffic_gb is not None else None,
        selected_devices_label=selected_devices_label,
        selected_traffic_label=selected_traffic_label,
        total_devices_label=total_devices_label,
        total_traffic_label=total_traffic_label,
        extra_price_text=extra_price_text,
        has_device_option=has_device_option,
        has_traffic_option=has_traffic_option,
    )

    builder = InlineKeyboardBuilder()

    add_option_rows(
        builder,
        email,
        device_int_options if has_device_option else [],
        selected_devices,
        traffic_int_options if has_traffic_option else [],
        selected_traffic_gb,
    )

    if has_device_pack_selected or has_traffic_pack_selected:
        builder.row(
            InlineKeyboardButton(
                text=CONFIRM_ADDON_BUTTON_TEXT.format(amount=extra_price_text),
                callback_data="key_addons_confirm",
            )
        )

    await send_addons_screen(callback, session, builder, text, data, email)
