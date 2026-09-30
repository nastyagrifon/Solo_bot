from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import NOTIFICATIONS_CONFIG
from database import get_keys, get_tariffs, get_tariffs_for_cluster
from database.access.resolution import resolve_user_optional
from database.models import Notification
from handlers.keys.utils import build_key_callback
from handlers.notifications.keyboards import build_tariffs_keyboard
from handlers.tariffs.buy.router import select_tariff_plan
from handlers.utils import format_discount_time_left, get_least_loaded_cluster
from logger import logger
from settings.buttons import MAIN_MENU, RENEW_KEY_NOTIFICATION
from settings.config import DISCOUNT_ACTIVE_HOURS
from settings.texts import (
    COLD_DISCOUNT_EXPIRED,
    COLD_DISCOUNT_FINAL_EXPIRED,
    COLD_DISCOUNT_OFFER_FINAL_TEXT,
    COLD_DISCOUNT_OFFER_PERSONAL_TEXT,
    COLD_DISCOUNT_TARIFF,
    COLD_DISCOUNT_TARIFFS_UNAVAILABLE,
    COLD_DISCOUNT_TARIFF_MAX,
    COLD_DISCOUNT_UNAVAILABLE,
    DISCOUNT_EXPIRED,
    DISCOUNT_FINAL_EXPIRED,
    DISCOUNT_OFFER_FINAL_TEXT,
    DISCOUNT_OFFER_PERSONAL_TEXT,
    DISCOUNT_TARIFF,
    DISCOUNT_TARIFFS_UNAVAILABLE,
    DISCOUNT_TARIFF_MAX,
    DISCOUNT_TARIFF_SELECT_ERROR,
    DISCOUNT_UNAVAILABLE,
)


router = Router()


@dataclass(frozen=True)
class DiscountOffer:
    notification_type: str
    group_code: str
    log_tag: str
    unavailable: str
    expired: str
    offer_text: str
    tariff_text: str
    tariffs_unavailable: str


async def _handle_discount(callback: CallbackQuery, session: AsyncSession, offer: DiscountOffer):
    tg_id = callback.from_user.id
    u = await resolve_user_optional(session, tg_id)
    if u is None:
        await callback.message.edit_text(offer.unavailable)
        return

    result = await session.execute(
        select(Notification.last_notification_time).where(
            Notification.user_id == u.id,
            Notification.notification_type == offer.notification_type,
        )
    )
    last_time = result.scalar_one_or_none()

    if not last_time:
        await callback.message.edit_text(offer.unavailable)
        return

    discount_active_hours = int(NOTIFICATIONS_CONFIG.get("DISCOUNT_ACTIVE_HOURS", DISCOUNT_ACTIVE_HOURS))

    now = datetime.now(timezone.utc)
    if last_time.tzinfo is None:
        last_time = last_time.replace(tzinfo=timezone.utc)
    if now - last_time > timedelta(hours=discount_active_hours):
        await callback.message.edit_text(offer.expired)
        return

    keys = await get_keys(session, tg_id)

    if keys and len(keys) > 0:
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text=RENEW_KEY_NOTIFICATION,
                callback_data=build_key_callback("renew_key", keys[0].client_id, keys[0].email),
            )
        )
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

        expires_at = last_time + timedelta(hours=discount_active_hours)
        await callback.message.edit_text(
            offer.offer_text.format(time_left=format_discount_time_left(expires_at, discount_active_hours)),
            reply_markup=builder.as_markup(),
        )
    else:
        tariffs = await get_tariffs(session=session, group_code=offer.group_code)
        if not tariffs:
            try:
                cluster_name = await get_least_loaded_cluster(session)
                cluster_tariffs = await get_tariffs_for_cluster(session, cluster_name)
                if cluster_tariffs:
                    group_code = cluster_tariffs[0].get("group_code")
                    if group_code:
                        logger.warning(f"[{offer.log_tag}] Нет тарифов {offer.group_code}, fallback на {group_code}")
                        tariffs = await get_tariffs(session=session, group_code=group_code)
            except Exception as e:
                logger.error(f"[{offer.log_tag}] Не удалось получить обычные тарифы: {e}")

            if not tariffs:
                await callback.message.edit_text(offer.tariffs_unavailable)
                return

        await callback.message.edit_text(
            offer.tariff_text,
            reply_markup=build_tariffs_keyboard(tariffs, prefix="discount_tariff"),
        )


def _register(callback_data: str, offer: DiscountOffer):
    @router.callback_query(F.data == callback_data)
    async def handler(callback: CallbackQuery, session: AsyncSession):
        await _handle_discount(callback, session, offer)

    return handler


handle_discount_entry = _register(
    "hot_lead_discount",
    DiscountOffer(
        "hot_lead_step_2", "discounts", "DISCOUNT",
        DISCOUNT_UNAVAILABLE, DISCOUNT_EXPIRED, DISCOUNT_OFFER_PERSONAL_TEXT,
        DISCOUNT_TARIFF, DISCOUNT_TARIFFS_UNAVAILABLE,
    ),
)


@router.callback_query(F.data.startswith("discount_tariff|"))
async def handle_discount_tariff_selection(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    try:
        tariff_id = int(callback.data.split("|")[1])
        original_data = callback.data
        object.__setattr__(callback, "data", f"select_tariff_plan|{tariff_id}")
        try:
            await select_tariff_plan(callback, session=session, state=state)
        finally:
            object.__setattr__(callback, "data", original_data)
    except Exception as e:
        logger.error(f"Ошибка при выборе скидочного тарифа: {e}")
        await callback.message.answer(DISCOUNT_TARIFF_SELECT_ERROR)


handle_ultra_discount = _register(
    "hot_lead_final_discount",
    DiscountOffer(
        "hot_lead_step_3", "discounts_max", "DISCOUNT_MAX",
        DISCOUNT_UNAVAILABLE, DISCOUNT_FINAL_EXPIRED, DISCOUNT_OFFER_FINAL_TEXT,
        DISCOUNT_TARIFF_MAX, DISCOUNT_TARIFFS_UNAVAILABLE,
    ),
)
handle_cold_discount_entry = _register(
    "cold_lead_discount",
    DiscountOffer(
        "cold_lead_step_2", "cold_discounts", "COLD_DISCOUNT",
        COLD_DISCOUNT_UNAVAILABLE, COLD_DISCOUNT_EXPIRED, COLD_DISCOUNT_OFFER_PERSONAL_TEXT,
        COLD_DISCOUNT_TARIFF, COLD_DISCOUNT_TARIFFS_UNAVAILABLE,
    ),
)
handle_cold_ultra_discount = _register(
    "cold_lead_final_discount",
    DiscountOffer(
        "cold_lead_step_3", "cold_discounts_max", "COLD_DISCOUNT_MAX",
        COLD_DISCOUNT_UNAVAILABLE, COLD_DISCOUNT_FINAL_EXPIRED, COLD_DISCOUNT_OFFER_FINAL_TEXT,
        COLD_DISCOUNT_TARIFF_MAX, COLD_DISCOUNT_TARIFFS_UNAVAILABLE,
    ),
)
