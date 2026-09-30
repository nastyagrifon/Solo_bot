from functools import partial

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from handlers.payments.keyboards import balance_fallback_kb
from handlers.payments.topup_flow import fast_amount_payment
from settings.texts import DEFAULT_PAYMENT_MESSAGE

from .service import (
    PARITYPAY_METHODS,
    generate_paritypay_payment_link,
    process_callback_pay_paritypay,
    router as service_router,
)


router = Router(name="paritypay_router")
router.include_router(service_router)


@router.callback_query(F.data == "pay_paritypay_sbp")
async def handle_pay_paritypay_sbp(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    await process_callback_pay_paritypay(callback_query, state, session, method_name="sbp")


def _prepare(method_name: str, amount: int):
    method = PARITYPAY_METHODS.get(method_name)
    if not method or not method["enable"]:
        return None, {"text": "❌ Способ оплаты ParityPay временно недоступен."}
    if amount < method["min_amount"]:
        return None, {
            "text": f"❌ Минимальная сумма для оплаты — {method['min_amount']}₽.",
            "reply_markup": balance_fallback_kb(),
        }
    return method, None


fast_payment = partial(
    fast_amount_payment,
    prepare=_prepare,
    payment_link=generate_paritypay_payment_link,
    payment_message=DEFAULT_PAYMENT_MESSAGE,
    log_prefix=lambda m: f"Ошибка при создании платежа ParityPay {m}",
    currency="RUB",
)

