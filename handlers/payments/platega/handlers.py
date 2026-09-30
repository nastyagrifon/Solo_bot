from functools import partial

from aiogram import Router
from sqlalchemy.ext.asyncio import AsyncSession

from handlers.payments.keyboards import balance_fallback_kb
from handlers.payments.topup_flow import fast_amount_payment
from settings.buttons import MAIN_MENU, PAY_2
from settings.texts import PLATEGA_PAYMENT_MESSAGE

from .service import (
    PLATEGA_METHODS,
    PLATEGA_MIN_AMOUNTS,
    _platega_credentials_ok,
    _platega_method_enabled,
    generate_platega_payment_link,
    router as service_router,
)


router = Router(name="platega_router")
router.include_router(service_router)


def _prepare(method_name: str, amount: int):
    method = PLATEGA_METHODS.get(method_name)
    if not method or not _platega_method_enabled(method):
        return None, {"text": "❌ Этот способ оплаты Platega временно недоступен."}
    if not _platega_credentials_ok():
        return None, {"text": "❌ Платёжная система Platega временно недоступна."}
    min_amount = PLATEGA_MIN_AMOUNTS.get(method_name, 10)
    if amount < min_amount:
        symbol = "$" if method["currency"] == "USD" else "₽"
        return None, {
            "text": f"❌ Минимальная сумма для оплаты через Platega — {symbol}{min_amount}.",
            "reply_markup": balance_fallback_kb(),
        }
    return method, None


_fast = partial(
    fast_amount_payment,
    prepare=_prepare,
    payment_link=generate_platega_payment_link,
    payment_message=PLATEGA_PAYMENT_MESSAGE,
    log_prefix=lambda m: f"[Platega] Ошибка при создании платежа ({m})",
)


async def handle_custom_amount_input_platega_sbp(
    event,
    session: AsyncSession,
    pay_button_text: str = PAY_2,
    main_menu_text: str = MAIN_MENU,
):
    await _fast(event, session, "sbp", pay_button_text, main_menu_text)


async def handle_custom_amount_input_platega_cards(
    event,
    session: AsyncSession,
    pay_button_text: str = PAY_2,
    main_menu_text: str = MAIN_MENU,
):
    await _fast(event, session, "cards", pay_button_text, main_menu_text)


async def handle_custom_amount_input_platega_int(
    event,
    session: AsyncSession,
    pay_button_text: str = PAY_2,
    main_menu_text: str = MAIN_MENU,
):
    await _fast(event, session, "int", pay_button_text, main_menu_text)


async def handle_custom_amount_input_platega_crypto(
    event,
    session: AsyncSession,
    pay_button_text: str = PAY_2,
    main_menu_text: str = MAIN_MENU,
):
    await _fast(event, session, "crypto", pay_button_text, main_menu_text)
