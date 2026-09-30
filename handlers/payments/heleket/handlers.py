from functools import partial

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from handlers.payments.keyboards import balance_fallback_kb
from handlers.payments.topup_flow import fast_amount_payment
from settings.texts import DEFAULT_PAYMENT_MESSAGE

from .service import (
    HELEKET_METHODS,
    generate_heleket_payment_link,
    process_callback_pay_heleket,
    router as service_router,
)


router = Router(name="heleket_router")
router.include_router(service_router)


@router.callback_query(F.data == "pay_heleket_crypto")
async def handle_pay_heleket_crypto(
    callback_query: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    """Обработчик оплаты через Heleket криптовалютой."""
    await process_callback_pay_heleket(callback_query, state, session, method_name="crypto")


def _prepare(method_name: str, amount: int):
    if amount < 10:
        return None, {
            "text": "❌ Минимальная сумма для оплаты криптовалютой — 10₽ (≈0.1$).",
            "reply_markup": balance_fallback_kb(),
        }
    enabled_methods = [m for m in HELEKET_METHODS.values() if m["enable"]]
    if not enabled_methods:
        return None, {"text": "❌ Способ оплаты Heleket временно недоступен."}
    return enabled_methods[0], None


fast_payment = partial(
    fast_amount_payment,
    prepare=_prepare,
    payment_link=generate_heleket_payment_link,
    payment_message=DEFAULT_PAYMENT_MESSAGE,
    log_prefix=lambda m: "Ошибка при создании платежа Heleket",
    currency="USD",
    bad_url="https://heleket.com/",
)
