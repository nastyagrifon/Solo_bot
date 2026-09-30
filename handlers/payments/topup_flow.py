"""Общий сценарий пополнения баланса для касс heleket, kassai, overpay, paritypay, platega, wata.

Экраны: меню способов → выбор суммы → своя сумма → ссылка на оплату. Всё, чем кассы
различаются (тексты, минималки, коллбэки, валюта ввода), передаётся параметрами
register_topup_flow — поведение каждой кассы остаётся прежним байт в байт.
"""

from collections.abc import Awaitable, Callable

import aiohttp

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User
from handlers.payments.keyboards import (
    balance_fallback_kb,
    build_amounts_keyboard,
    parse_amount_from_callback,
    pay_keyboard,
    payment_options_for_user,
)
from handlers.utils import edit_or_send_message
from logger import logger
from services.payments.currency_rates import format_for_user, pick_currency, to_rub
from settings.buttons import BACK, PAY_2


METHOD_UNAVAILABLE = "Ошибка: выбранный способ оплаты недоступен."
PAYMENT_CREATE_FAILED = "❌ Произошла ошибка при создании платежа. Попробуйте позже или выберите другой способ оплаты."
INVALID_AMOUNT = "❌ Некорректная сумма. Введите целое число больше 0."

PaymentLink = Callable[[int, int, dict, AsyncSession], Awaitable[str | None]]


async def _get_user_language(session: AsyncSession, tg_id: int) -> str | None:
    result = await session.execute(select(User.language_code).where(User.tg_id == tg_id))
    return result.scalar_one_or_none()


def _empty_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[])


def register_topup_flow(
    router: Router,
    *,
    prefix: str,
    methods: dict[str, dict],
    states: type[StatesGroup],
    enabled: Callable[[dict], bool],
    payment_link: PaymentLink,
    payment_message: str,
    min_amount: Callable[[str, dict], int],
    input_min_text: Callable[[str], str],
    amount_min_text: Callable[[str], str],
    entry_error_log: Callable[[types.CallbackQuery, Exception], str],
    multicurrency_input: bool,
    link_by_chat_id: bool,
    custom_back_cb: str,
    custom_requires_method: bool = False,
    entry_log: str | None = None,
    menu_text: str | None = None,
    method_back_cb: str | None = None,
    autopick_single: bool = False,
    entry_cb: str | None = None,
    per_method_entry: bool = False,
    credentials_ok: Callable[[], bool] | None = None,
    credentials_log: str = "",
    credentials_text: str = "",
    credentials_on_amount: bool = False,
):
    """Регистрирует хендлеры пополнения на router и возвращает входную функцию кассы.

    prefix — префикс коллбэков и ключа FSM (`{prefix}_method|`, `{prefix}_custom_amount|`,
    `{prefix}_{метод}_amount|`, данные `{prefix}_method`).
    payment_link(amount_rub, tg_id, method, session) — URL или None при ошибке.
    min_amount(метод, method) — минималка в рублях; при вводе в долларах она всегда 1.
    input_min_text / amount_min_text(метод) — шаблон с {symbol} и {min}.
    multicurrency_input — своя сумма вводится в валюте пользователя (RUB/USD) с пересчётом в рубли;
    иначе только рубли.
    link_by_chat_id — ссылка создаётся на chat.id, иначе на from_user.id.
    custom_back_cb — «Назад» с экрана своей суммы, шаблон с {method}.
    menu_text — есть меню способов (и хендлер `{prefix}_method|` с «Назад» на method_back_cb);
    None — вход только с конкретным способом.
    entry_cb — вход в меню по точному коллбэку (с флагом popup);
    per_method_entry — вход по `pay_{prefix}_{метод}` для каждого способа.
    credentials_ok — проверка реквизитов на входе; credentials_on_amount — и на шагах суммы тоже.
    """
    method_key = f"{prefix}_method"
    amount_prefixes = [f"{prefix}_{name}" for name in methods]

    def is_available(method: dict | None) -> bool:
        if not method or not enabled(method):
            return False
        return not (credentials_on_amount and credentials_ok and not credentials_ok())

    def display_currency(method: dict) -> str:
        return method.get("currency", "RUB")

    async def amounts_keyboard(session: AsyncSession, tg_id: int, method_name: str, method: dict, back_cb: str):
        language_code = await _get_user_language(session, tg_id)
        opts = await payment_options_for_user(session, tg_id, language_code, force_currency=display_currency(method))
        return build_amounts_keyboard(
            prefix=f"{prefix}_{method_name}",
            pattern="{prefix}_amount|{price}",
            back_cb=back_cb,
            custom_cb=f"{prefix}_custom_amount|{method_name}",
            opts=opts,
        )

    async def send_payment(
        target: types.Message,
        state: FSMContext,
        session: AsyncSession,
        user_id: int,
        amount_rub: int,
        method: dict,
        language_code: str | None,
        lazy_language: bool,
    ):
        await state.update_data(amount=amount_rub)
        link_tg_id = target.chat.id if link_by_chat_id else user_id
        payment_url = await payment_link(amount_rub, link_tg_id, method, session)
        if not payment_url:
            await edit_or_send_message(target_message=target, text=PAYMENT_CREATE_FAILED, reply_markup=_empty_kb())
            return

        confirm_keyboard = pay_keyboard(payment_url, pay_text=PAY_2, back_cb="balance")
        if lazy_language:
            language_code = await _get_user_language(session, user_id)
        amount_text = await format_for_user(
            session, user_id, float(amount_rub), language_code, force_currency=display_currency(method)
        )
        await edit_or_send_message(
            target_message=target,
            text=payment_message.format(amount=amount_text),
            reply_markup=confirm_keyboard,
        )
        await state.set_state(states.waiting_for_payment_confirmation)

    async def entry(
        callback_query: types.CallbackQuery,
        state: FSMContext,
        session: AsyncSession,
        method_name: str | None = None,
    ):
        try:
            tg_id = callback_query.from_user.id
            if entry_log:
                logger.info(entry_log.format(tg_id=tg_id))
            await state.clear()

            if autopick_single and not method_name:
                enabled_methods = [name for name, m in methods.items() if enabled(m)]
                if len(enabled_methods) == 1:
                    method_name = enabled_methods[0]

            if method_name or menu_text is None:
                method = methods.get(method_name)
                if not method or not enabled(method):
                    await edit_or_send_message(
                        target_message=callback_query.message, text=METHOD_UNAVAILABLE, reply_markup=_empty_kb()
                    )
                    return

                if credentials_ok and not credentials_ok():
                    logger.error(credentials_log)
                    await edit_or_send_message(
                        target_message=callback_query.message, text=credentials_text, reply_markup=_empty_kb()
                    )
                    return

                builder = await amounts_keyboard(session, tg_id, method_name, method, "balance")
                await edit_or_send_message(target_message=callback_query.message, text=method["desc"], reply_markup=builder)
                await state.update_data(
                    **{method_key: method_name},
                    message_id=callback_query.message.message_id,
                    chat_id=callback_query.message.chat.id,
                )
                await state.set_state(states.choosing_amount)
                return

            builder = InlineKeyboardBuilder()
            for name, method in methods.items():
                if enabled(method):
                    builder.row(InlineKeyboardButton(text=method["button"], callback_data=f"{prefix}_method|{name}"))
            builder.row(InlineKeyboardButton(text=BACK, callback_data="balance"))

            await edit_or_send_message(
                target_message=callback_query.message,
                text=menu_text,
                reply_markup=builder.as_markup(),
            )
            await state.update_data(
                message_id=callback_query.message.message_id,
                chat_id=callback_query.message.chat.id,
            )
            await state.set_state(states.choosing_method)

        except Exception as e:
            logger.error(entry_error_log(callback_query, e))
            await callback_query.answer("Произошла ошибка при инициализации платежа. Попробуйте позже.", show_alert=True)

    async def process_method_selection(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
        method_name = callback_query.data.split("|")[1]
        method = methods.get(method_name)
        if not method or not enabled(method):
            await edit_or_send_message(
                target_message=callback_query.message, text=METHOD_UNAVAILABLE, reply_markup=_empty_kb()
            )
            return

        await state.update_data(**{method_key: method_name})
        builder = await amounts_keyboard(session, callback_query.from_user.id, method_name, method, method_back_cb)
        await edit_or_send_message(target_message=callback_query.message, text=method["desc"], reply_markup=builder)
        await state.update_data(message_id=callback_query.message.message_id, chat_id=callback_query.message.chat.id)
        await state.set_state(states.choosing_amount)

    async def process_custom_amount_button(
        callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession
    ):
        method_name = callback_query.data.split("|")[1]
        if custom_requires_method and not methods.get(method_name):
            return
        await state.update_data(**{method_key: method_name})

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=BACK, callback_data=custom_back_cb.format(method=method_name)))

        currency_text = "рублях (₽)"
        if multicurrency_input:
            language_code = await _get_user_language(session, callback_query.from_user.id)
            if pick_currency(language_code) != "RUB":
                currency_text = "долларах ($)"
        await edit_or_send_message(
            target_message=callback_query.message,
            text=f"Пожалуйста, введите сумму пополнения в {currency_text}.",
            reply_markup=builder.as_markup(),
        )
        await state.set_state(states.entering_custom_amount)

    async def handle_custom_amount_input(message: types.Message, state: FSMContext, session: AsyncSession):
        data = await state.get_data()
        method_name = data.get(method_key)
        method = methods.get(method_name)
        if not is_available(method):
            await edit_or_send_message(target_message=message, text=METHOD_UNAVAILABLE, reply_markup=_empty_kb())
            return

        language_code = None
        currency = "RUB"
        if multicurrency_input:
            language_code = await _get_user_language(session, message.from_user.id)
            currency = pick_currency(language_code)

        try:
            user_amount = int(message.text.strip())
            if user_amount <= 0:
                raise ValueError

            min_value = 1 if currency == "USD" else min_amount(method_name, method)
            if user_amount < min_value:
                symbol = "$" if currency == "USD" else "₽"
                await edit_or_send_message(
                    target_message=message,
                    text=input_min_text(method_name).format(symbol=symbol, min=min_value),
                    reply_markup=balance_fallback_kb(),
                )
                return
        except Exception:
            await edit_or_send_message(target_message=message, text=INVALID_AMOUNT, reply_markup=_empty_kb())
            return

        if currency == "RUB":
            amount_rub = user_amount
        else:
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            async with aiohttp.ClientSession(timeout=timeout) as session_http:
                amount_rub = int(await to_rub(user_amount, "USD", session=session_http))

        await send_payment(
            message,
            state,
            session,
            message.from_user.id,
            amount_rub,
            method,
            language_code,
            lazy_language=not multicurrency_input,
        )

    async def process_amount_selection(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
        amount = parse_amount_from_callback(callback_query.data, prefixes=amount_prefixes)
        if amount is None:
            await edit_or_send_message(
                target_message=callback_query.message, text="Некорректная сумма.", reply_markup=_empty_kb()
            )
            return

        method_name = next(
            (p.removeprefix(f"{prefix}_") for p in amount_prefixes if callback_query.data.startswith(f"{p}_amount|")),
            None,
        )
        method = methods.get(method_name) if method_name else None
        if not is_available(method):
            await edit_or_send_message(
                target_message=callback_query.message, text=METHOD_UNAVAILABLE, reply_markup=_empty_kb()
            )
            return

        min_value = min_amount(method_name, method)
        if amount < min_value:
            symbol = "$" if method.get("currency") == "USD" else "₽"
            await edit_or_send_message(
                target_message=callback_query.message,
                text=amount_min_text(method_name).format(symbol=symbol, min=min_value),
                reply_markup=balance_fallback_kb(),
            )
            return

        await send_payment(
            callback_query.message,
            state,
            session,
            callback_query.from_user.id,
            amount,
            method,
            None,
            lazy_language=True,
        )

    def entry_for(method_name: str):
        async def handler(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
            await entry(callback_query, state, session, method_name)

        return handler

    if entry_cb:
        router.callback_query(F.data == entry_cb, flags={"popup": True})(entry)
    if per_method_entry:
        for name in methods:
            router.callback_query(F.data == f"pay_{prefix}_{name}")(entry_for(name))
    if menu_text is not None:
        router.callback_query(F.data.startswith(f"{prefix}_method|"))(process_method_selection)
    router.callback_query(F.data.startswith(f"{prefix}_custom_amount|"))(process_custom_amount_button)
    router.message(states.entering_custom_amount)(handle_custom_amount_input)
    amount_filter = F.data.startswith(f"{amount_prefixes[0]}_amount|")
    for p in amount_prefixes[1:]:
        amount_filter = amount_filter | F.data.startswith(f"{p}_amount|")
    router.callback_query(amount_filter)(process_amount_selection)

    return entry
