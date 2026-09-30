from functools import partial

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from handlers.payments.keyboards import balance_fallback_kb
from handlers.payments.topup_flow import fast_amount_payment
from settings.texts import DEFAULT_PAYMENT_MESSAGE

from .service import (
    WATA_METHODS,
    WATA_MIN_AMOUNTS,
    _wata_method_enabled,
    generate_wata_payment_link,
    process_callback_pay_wata,
    router as service_router,
)


router = Router(name="wata_router")
router.include_router(service_router)


@router.callback_query(F.data == "pay_wata_ru")
async def handle_pay_wata_ru(
    callback_query: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    await process_callback_pay_wata(callback_query, state, session, method_name="ru")


@router.callback_query(F.data == "pay_wata_int")
async def handle_pay_wata_int(
    callback_query: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    await process_callback_pay_wata(callback_query, state, session, method_name="int")


def _prepare(method_name: str, amount: int):
    method = WATA_METHODS.get(method_name)
    if not method or not _wata_method_enabled(method):
        return None, {"text": "❌ Этот способ оплаты Wata временно недоступен."}
    min_amount = WATA_MIN_AMOUNTS.get(method_name, 10)
    if amount < min_amount:
        symbol = "$" if method["currency"] == "USD" else "₽"
        return None, {
            "text": f"❌ Минимальная сумма для оплаты через WATA — {symbol}{min_amount}.",
            "reply_markup": balance_fallback_kb(),
        }
    return method, None


fast_payment = partial(
    fast_amount_payment,
    prepare=_prepare,
    payment_link=generate_wata_payment_link,
    payment_message=DEFAULT_PAYMENT_MESSAGE,
    log_prefix=lambda m: f"[WATA] Ошибка при создании платежа ({m})",
)
