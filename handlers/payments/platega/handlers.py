from functools import partial

from aiogram import Router

from handlers.payments.keyboards import balance_fallback_kb
from handlers.payments.topup_flow import fast_amount_payment
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


fast_payment = partial(
    fast_amount_payment,
    prepare=_prepare,
    payment_link=generate_platega_payment_link,
    payment_message=PLATEGA_PAYMENT_MESSAGE,
    log_prefix=lambda m: f"[Platega] Ошибка при создании платежа ({m})",
)
