from functools import partial

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from handlers.payments.keyboards import balance_fallback_kb
from handlers.payments.topup_flow import fast_amount_payment
from settings.buttons import MAIN_MENU, PAY_2
from settings.texts import DEFAULT_PAYMENT_MESSAGE

from .service import (
    KASSAI_METHODS,
    KASSAI_MIN_AMOUNTS,
    KASSAI_MIN_TEXTS,
    generate_kassai_payment_link,
    process_callback_pay_kassai,
    router as service_router,
)


router = Router(name="kassai_router")
router.include_router(service_router)


@router.callback_query(F.data == "pay_kassai_cards")
async def handle_pay_kassai_cards(
    callback_query: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    """Обработчик оплаты через KassaI картами."""
    await process_callback_pay_kassai(callback_query, state, session, method_name="cards")


@router.callback_query(F.data == "pay_kassai_sbp")
async def handle_pay_kassai_sbp(
    callback_query: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    """Обработчик оплаты через KassaI СБП."""
    await process_callback_pay_kassai(callback_query, state, session, method_name="sbp")


def _prepare(method_name: str, amount: int):
    min_amount = KASSAI_MIN_AMOUNTS.get(method_name, 10)
    if amount < min_amount:
        text = f"❌ Минимальная сумма для {KASSAI_MIN_TEXTS.get(method_name, 'оплаты ')} — {min_amount}₽."
        return None, {"text": text, "reply_markup": balance_fallback_kb()}

    method = KASSAI_METHODS.get(method_name)
    if not method or not method["enable"]:
        method_label = "картами" if method_name == "cards" else "через СБП"
        return None, {"text": f"❌ Оплата {method_label} KassaAI временно недоступна."}
    return method, None


_fast = partial(
    fast_amount_payment,
    prepare=_prepare,
    payment_link=generate_kassai_payment_link,
    payment_message=DEFAULT_PAYMENT_MESSAGE,
    log_prefix=lambda m: f"Ошибка при создании платежа KassaAI {'Cards' if m == 'cards' else 'SBP'}",
    currency="RUB",
    bad_url="https://fk.life/",
)


async def handle_custom_amount_input_kassai_cards(
    event,
    session: AsyncSession,
    pay_button_text: str = PAY_2,
    main_menu_text: str = MAIN_MENU,
):
    """Функция быстрого потока для KassaI Cards."""
    await _fast(event, session, "cards", pay_button_text, main_menu_text)


async def handle_custom_amount_input_kassai_sbp(
    event,
    session: AsyncSession,
    pay_button_text: str = PAY_2,
    main_menu_text: str = MAIN_MENU,
):
    """Функция быстрого потока для KassaI SBP."""
    await _fast(event, session, "sbp", pay_button_text, main_menu_text)
