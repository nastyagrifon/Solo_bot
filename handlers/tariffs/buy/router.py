from datetime import datetime, timezone
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import get_tariff_by_id
from database.notifications import check_cold_lead_discount, check_hot_lead_discount
from handlers.utils import edit_or_send_message, safe_answer_callback
from hooks.hooks import run_hooks
from hooks.processors import process_check_discount_validity
from logger import logger
from services.tariffs.cooldown import format_cooldown_left, get_tariff_cooldown_remaining
from services.tariffs.tariff_display import GB
from settings.buttons import MAIN_MENU
from settings.texts import (
    TARIFF_COOLDOWN_MESSAGE,
)

from .config import TariffUserConfigState, start_user_tariff_configurator
from .purchase import finalize_config_and_purchase, proceed_purchase_with_values
from .screens import render_user_config_screen


router = Router()


CREATING_KEY_BUTTON_TEXT = "⏳ Подождите..."


@router.callback_query(F.data.startswith("select_tariff_plan|"))
async def select_tariff_plan(callback_query: CallbackQuery, session: Any, state: FSMContext):
    """Обрабатывает выбор тарифа пользователем."""
    tg_id = callback_query.from_user.id
    tariff_id = int(callback_query.data.split("|")[1])

    logger.info("[TARIFF_CFG] select_tariff_plan: tg_id={} tariff_id={}", tg_id, tariff_id)

    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="❌ Указанный тариф не найден.",
        )
        await safe_answer_callback(callback_query)
        logger.warning(f"[TARIFF_CFG] select_tariff_plan tariff_not_found: tariff_id={tariff_id}")
        return

    cooldown_left = await get_tariff_cooldown_remaining(
        session, tg_id, tariff.get("id"), tariff.get("cooldown_days", 0)
    )
    if cooldown_left > 0:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
        await edit_or_send_message(
            target_message=callback_query.message,
            text=TARIFF_COOLDOWN_MESSAGE.format(
                days=int(tariff.get("cooldown_days") or 0),
                left=format_cooldown_left(cooldown_left),
            ),
            reply_markup=builder.as_markup(),
        )
        await safe_answer_callback(callback_query)
        logger.info(
            f"[TARIFF_CFG] select_tariff_plan cooldown_block: tg_id={tg_id} tariff_id={tariff_id} left={cooldown_left}s"
        )
        return

    discount_info = await check_hot_lead_discount(session, tg_id)
    if tariff.get("group_code") in ["discounts", "discounts_max"]:
        if not discount_info.get("available") or datetime.now(timezone.utc) >= discount_info["expires_at"]:
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
            await edit_or_send_message(
                target_message=callback_query.message,
                text="❌ Скидка недоступна или истекла. Пожалуйста, выберите тариф заново.",
                reply_markup=builder.as_markup(),
            )
            await safe_answer_callback(callback_query)
            logger.info(
                "[TARIFF_CFG] select_tariff_plan discount_invalid: "
                f"tg_id={tg_id} tariff_id={tariff_id} info={discount_info}"
            )
            return
    elif tariff.get("group_code") in ["cold_discounts", "cold_discounts_max"]:
        cold_discount_info = await check_cold_lead_discount(session, tg_id)
        if not cold_discount_info.get("available") or datetime.now(timezone.utc) >= cold_discount_info["expires_at"]:
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
            await edit_or_send_message(
                target_message=callback_query.message,
                text="❌ Скидка недоступна или истекла. Пожалуйста, выберите тариф заново.",
                reply_markup=builder.as_markup(),
            )
            await safe_answer_callback(callback_query)
            logger.info(
                "[TARIFF_CFG] select_tariff_plan cold_discount_invalid: "
                f"tg_id={tg_id} tariff_id={tariff_id} info={cold_discount_info}"
            )
            return

    validity_result = await process_check_discount_validity(
        chat_id=tg_id,
        admin=False,
        session=session,
        tariff_group=tariff.get("group_code"),
    )
    if validity_result:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
        await edit_or_send_message(
            target_message=callback_query.message,
            text=validity_result["message"],
            reply_markup=builder.as_markup(),
        )
        await safe_answer_callback(callback_query)
        logger.info(
            "[TARIFF_CFG] select_tariff_plan discount_validity_failed: "
            f"tg_id={tg_id} tariff_id={tariff_id} result={validity_result}"
        )
        return

    data = await state.get_data()
    if data.get("renew_mode") != "renew":
        await state.update_data(renew_mode=None)

    if tariff.get("configurable"):
        logger.info(f"[TARIFF_CFG] select_tariff_plan configurable: tg_id={tg_id} tariff_id={tariff_id}")
        try:
            await start_user_tariff_configurator(callback_query, session=session, state=state, tariff=tariff)
        except Exception as error:
            logger.error(f"[TARIFF_CFG] error_in_configurator: tariff_id={tariff_id} error={error}")
            await edit_or_send_message(
                target_message=callback_query.message,
                text="❌ Ошибка конфигурации тарифа. Попробуйте позже.",
                reply_markup=None,
            )
        return

    duration_days = int(tariff.get("duration_days") or 30)
    price_rub = int(tariff.get("price_rub") or 0)
    selected_device_limit = tariff.get("device_limit")

    raw_traffic_limit = tariff.get("traffic_limit")
    selected_traffic_gb = None
    if raw_traffic_limit:
        raw_traffic_limit = int(raw_traffic_limit)
        if raw_traffic_limit >= GB:
            selected_traffic_gb = int(raw_traffic_limit / GB)
        else:
            selected_traffic_gb = raw_traffic_limit

    logger.info(
        "[TARIFF_CFG] select_tariff_plan fixed_tariff: "
        f"tg_id={tg_id} tariff_id={tariff_id} duration_days={duration_days} "
        f"price_rub={price_rub} device_limit={selected_device_limit} "
        f"selected_traffic_gb={selected_traffic_gb}"
    )

    await proceed_purchase_with_values(
        callback_query=callback_query,
        session=session,
        state=state,
        tariff=tariff,
        duration_days=duration_days,
        price_rub=price_rub,
        selected_device_limit=selected_device_limit,
        selected_traffic_gb=selected_traffic_gb,
    )


@router.callback_query(
    F.data.startswith("cfg_user_devices|"),
    TariffUserConfigState.configuring,
)
async def handle_user_devices_choice(callback: CallbackQuery, state: FSMContext, session: Any):
    """Обрабатывает выбор лимита устройств в конфигураторе."""
    await safe_answer_callback(callback)
    _, _tariff_id_str, devices_str = callback.data.split("|", 2)
    devices = int(devices_str)

    data = await state.get_data()
    if data.get("config_tariff_id") is None:
        await state.update_data(config_tariff_id=int(_tariff_id_str))
    current = data.get("config_selected_device_limit")
    if current is not None and int(current) == devices:
        return

    await state.update_data(config_selected_device_limit=devices)
    await render_user_config_screen(callback, state, session)


@router.callback_query(
    F.data.startswith("cfg_user_traffic|"),
    TariffUserConfigState.configuring,
)
async def handle_user_traffic_choice(callback: CallbackQuery, state: FSMContext, session: Any):
    """Обрабатывает выбор лимита трафика в конфигураторе."""
    await safe_answer_callback(callback)
    _, _tariff_id_str, traffic_str = callback.data.split("|", 2)
    traffic = int(traffic_str)

    data = await state.get_data()
    if data.get("config_tariff_id") is None:
        await state.update_data(config_tariff_id=int(_tariff_id_str))
    current = data.get("config_selected_traffic_gb")
    if current is not None and int(current) == traffic:
        return

    await state.update_data(config_selected_traffic_gb=traffic)
    await render_user_config_screen(callback, state, session=session)


async def _active_trial_key(session: Any, tg_id: int) -> dict | None:
    """Единственный действующий пробный ключ клиента в режиме «одна подписка».

    Кнопки «Купить» (create_key) приходят и клиенту с пробником — в
    уведомлениях, в меню модулей. Без этой проверки покупка заводит ему второй
    ключ, хотя режим обещает одну подписку. Во всех сомнительных случаях
    (ключей не один, пробник истёк, тариф не прочитан) возвращаем None — и
    покупка идёт штатно: лишний ключ видно и можно поправить, потерянную
    оплату — нет.
    """
    from core.bootstrap import MODES_CONFIG
    from database import get_keys

    if not bool(MODES_CONFIG.get("SINGLE_SUBSCRIPTION_MODE", False)):
        return None
    keys = await get_keys(session, tg_id) or []
    if len(keys) != 1:
        return None
    key = keys[0]
    client_id, email = getattr(key, "client_id", None), getattr(key, "email", None)
    tariff_id, expiry = getattr(key, "tariff_id", None), getattr(key, "expiry_time", None)
    if not (client_id and email and tariff_id and expiry):
        return None
    if int(expiry) <= int(datetime.now(timezone.utc).timestamp() * 1000):
        return None
    tariff = await get_tariff_by_id(session, int(tariff_id))
    if str((tariff or {}).get("group_code") or "").lower() != "trial":
        return None
    return {"client_id": str(client_id), "email": str(email)}


@router.callback_query(F.data.startswith("cfg_user_confirm|"), TariffUserConfigState.configuring)
async def handle_user_config_confirm(callback: CallbackQuery, state: FSMContext, session: Any):
    """Подтверждает выбор параметров тарифа и запускает покупку.

    Клиенту с действующим пробником в режиме «одна подписка» покупка
    оформляется продлением его же ключа штатным обработчиком продления:
    сроки, цена и перенос опций считаются там, а не здесь.
    """
    tg_id = callback.from_user.id
    tariff_id = int(callback.data.split("|", 1)[1])
    logger.info(f"[TARIFF_CFG] handle_user_config_confirm: tg_id={tg_id}")

    # Точка для модулей: до этого им приходилось перехватывать нажатие
    # целиком. Хук, вернувший {"handled": True}, забирает покупку себе;
    # сбой хука покупку не ломает — run_hooks его глотает и пишет в лог.
    results = await run_hooks(
        "purchase_confirm", callback=callback, state=state, session=session, tg_id=tg_id, tariff_id=tariff_id
    )
    if any(isinstance(r, dict) and r.get("handled") for r in results):
        logger.info(f"[TARIFF_CFG] покупку забрал модуль: tg_id={tg_id} tariff_id={tariff_id}")
        return

    trial = await _active_trial_key(session, tg_id)
    if trial:
        from handlers.keys.renew.router import handle_renew_config_confirm

        logger.info(f"[TARIFF_CFG] пробный ключ продлевается вместо второго: tg_id={tg_id} tariff_id={tariff_id}")
        await state.update_data(
            renew_mode="renew",
            renew_client_id=trial["client_id"],
            renew_key_name=trial["email"],
            renew_tariff_id=tariff_id,
        )
        await handle_renew_config_confirm(callback, state, session)
        return

    await finalize_config_and_purchase(callback, state, session=session)
