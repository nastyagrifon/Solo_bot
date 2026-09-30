from functools import partial

from aiogram import Router
from sqlalchemy.ext.asyncio import AsyncSession

from handlers.payments.keyboards import balance_fallback_kb
from handlers.payments.topup_flow import fast_amount_payment
from settings.buttons import MAIN_MENU, PAY_2
from settings.texts import OVERPAY_PAYMENT_MESSAGE

from .service import (
    OVERPAY_METHODS,
    OVERPAY_MIN_AMOUNT,
    _overpay_credentials_ok,
    _overpay_method_enabled,
    _payment_link,
    router as service_router,
)


router = Router(name="overpay_router")
router.include_router(service_router)


def _prepare(method_name: str, amount: int):
    method = OVERPAY_METHODS.get(method_name)
    if not method or not _overpay_method_enabled(method):
        return None, {"text": "❌ Этот способ оплаты Overpay временно недоступен."}
    if not _overpay_credentials_ok():
        return None, {"text": "❌ Платежная система Overpay временно недоступна."}
    if amount < OVERPAY_MIN_AMOUNT:
        return None, {
            "text": f"❌ Минимальная сумма для оплаты — {OVERPAY_MIN_AMOUNT}₽.",
            "reply_markup": balance_fallback_kb(),
        }
    return method, None


_fast = partial(
    fast_amount_payment,
    prepare=_prepare,
    payment_link=_payment_link,
    payment_message=OVERPAY_PAYMENT_MESSAGE,
    log_prefix=lambda m: f"[Overpay] Ошибка при создании платежа ({m})",
    currency="RUB",
)


async def handle_custom_amount_input_overpay_cards(
    event,
    session: AsyncSession,
    pay_button_text: str = PAY_2,
    main_menu_text: str = MAIN_MENU,
):
    await _fast(event, session, "cards", pay_button_text, main_menu_text)


async def handle_custom_amount_input_overpay_sbp(
    event,
    session: AsyncSession,
    pay_button_text: str = PAY_2,
    main_menu_text: str = MAIN_MENU,
):
    await _fast(event, session, "sbp", pay_button_text, main_menu_text)
