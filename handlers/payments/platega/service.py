import time

from decimal import ROUND_HALF_UP, Decimal

import aiohttp

from aiogram import Router
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import PAYMENTS_CONFIG
from database import add_payment, async_session_maker
from handlers.payments.topup_flow import register_topup_flow
from logger import logger
from services.payments.currency_rates import get_rub_rate
from services.payments.payment_links import register_payment_creator
from settings.buttons import (
    PLATEGA_CARDS,
    PLATEGA_CRYPTO,
    PLATEGA_INT,
    PLATEGA_SBP,
)
from settings.config import (
    PLATEGA_API_SECRET,
    PLATEGA_FAIL_URL,
    PLATEGA_MERCHANT_ID,
    PLATEGA_SUCCESS_URL,
)
from settings.texts import (
    PLATEGA_CARDS_DESCRIPTION,
    PLATEGA_CRYPTO_DESCRIPTION,
    PLATEGA_INT_DESCRIPTION,
    PLATEGA_PAYMENT_MESSAGE,
    PLATEGA_PAYMENT_TITLE,
    PLATEGA_SBP_DESCRIPTION,
)


router = Router()


PLATEGA_API_URL = "https://app.platega.io/transaction/process"

PLATEGA_MIN_AMOUNTS: dict[str, int] = {
    "sbp": 10,
    "cards": 10,
    "int": 1,
    "crypto": 1,
}


class ReplenishBalancePlatega(StatesGroup):
    choosing_amount = State()
    waiting_for_payment_confirmation = State()
    entering_custom_amount = State()


PLATEGA_METHODS: dict[str, dict] = {
    "sbp": {
        "provider_key": "PLATEGA_SBP",
        "method_code": 2,
        "currency": "RUB",
        "button": PLATEGA_SBP,
        "desc": PLATEGA_SBP_DESCRIPTION,
    },
    "cards": {
        "provider_key": "PLATEGA_CARDS",
        "method_code": 11,
        "currency": "RUB",
        "button": PLATEGA_CARDS,
        "desc": PLATEGA_CARDS_DESCRIPTION,
    },
    "int": {
        "provider_key": "PLATEGA_INT",
        "method_code": 12,
        "currency": "USD",
        "button": PLATEGA_INT,
        "desc": PLATEGA_INT_DESCRIPTION,
    },
    "crypto": {
        "provider_key": "PLATEGA_CRYPTO",
        "method_code": 13,
        "currency": "USD",
        "button": PLATEGA_CRYPTO,
        "desc": PLATEGA_CRYPTO_DESCRIPTION,
    },
}


def _platega_method_enabled(method: dict) -> bool:
    return bool(PAYMENTS_CONFIG.get(method["provider_key"], False))


def _platega_credentials_ok() -> bool:
    return bool((PLATEGA_MERCHANT_ID or "").strip()) and bool((PLATEGA_API_SECRET or "").strip())


process_callback_pay_platega = register_topup_flow(
    router,
    prefix="platega",
    methods=PLATEGA_METHODS,
    states=ReplenishBalancePlatega,
    enabled=_platega_method_enabled,
    payment_link=lambda amount, tg_id, method, session: generate_platega_payment_link(amount, tg_id, method, session),
    payment_message=PLATEGA_PAYMENT_MESSAGE,
    min_amount=lambda name, method: PLATEGA_MIN_AMOUNTS.get(name, 10),
    input_min_text=lambda name: "❌ Минимальная сумма для оплаты через Platega — {symbol}{min}.",
    amount_min_text=lambda name: "❌ Минимальная сумма для оплаты через Platega — {symbol}{min}.",
    entry_error_log=lambda cq, e: f"[Platega] Ошибка в process_callback_pay_platega для {cq.from_user.id}: {e}",
    multicurrency_input=True,
    link_by_chat_id=True,
    custom_back_cb="pay_platega_{method}",
    custom_requires_method=True,
    per_method_entry=True,
    credentials_ok=_platega_credentials_ok,
    credentials_log="[Platega] Не заданы PLATEGA_MERCHANT_ID / PLATEGA_API_SECRET",
    credentials_text="Ошибка: платёжная система временно недоступна.",
)


def _platega_headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-MerchantId": PLATEGA_MERCHANT_ID,
        "X-Secret": PLATEGA_API_SECRET,
    }


async def generate_platega_payment_link(
    amount: int,
    tg_id: int,
    method: dict,
    session: AsyncSession | None = None,
    *,
    payment_id: str | None = None,
    success_url: str | None = None,
    failure_url: str | None = None,
    metadata: dict | None = None,
) -> str | None:
    if not _platega_credentials_ok():
        logger.error("[Platega] Не заданы PLATEGA_MERCHANT_ID / PLATEGA_API_SECRET")
        return None

    method_code = int(method.get("method_code") or 0)
    currency = str(method.get("currency") or "RUB").upper()
    method_name = next((k for k, v in PLATEGA_METHODS.items() if v is method), None) or ""

    unique_order_id = payment_id or f"plg_{int(time.time())}_{tg_id}_{int(amount)}"

    pending_metadata = dict(metadata or {})
    pending_metadata.setdefault("provider", "platega")
    pending_metadata.setdefault("platega_method", method_name)
    pending_metadata.setdefault("platega_method_code", method_code)

    pending_original_amount: float | None = None

    if currency == "RUB":
        api_amount = float(int(amount))
    else:
        try:
            timeout = aiohttp.ClientTimeout(total=15, connect=10)
            async with aiohttp.ClientSession(timeout=timeout) as http_rates:
                rate = await get_rub_rate(currency, session=http_rates)
            usd_amount = (Decimal(str(amount)) * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            api_amount = float(usd_amount)
            pending_original_amount = float(usd_amount)
            pending_metadata["platega_currency"] = currency
        except Exception as e:
            logger.error(f"[Platega] Не удалось сконвертировать {amount} RUB → {currency}: {e}")
            return None

    body: dict = {
        "paymentMethod": method_code,
        "paymentDetails": {
            "amount": api_amount,
            "currency": currency,
        },
        "description": PLATEGA_PAYMENT_TITLE,
        "payload": unique_order_id,
    }
    ret_url = success_url or PLATEGA_SUCCESS_URL or ""
    fail_url = failure_url or PLATEGA_FAIL_URL or ""
    if ret_url:
        body["return"] = ret_url
    if fail_url:
        body["failedUrl"] = fail_url

    timeout = aiohttp.ClientTimeout(total=60, connect=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http_session:
            async with http_session.post(PLATEGA_API_URL, headers=_platega_headers(), json=body) as resp:
                if resp.status not in (200, 201):
                    try:
                        error_json = await resp.json(content_type=None)
                        logger.error(f"[Platega] API error: status={resp.status}, response={error_json}")
                    except Exception:
                        text = await resp.text()
                        logger.error(f"[Platega] API error: status={resp.status}, non-JSON: {text[:300]}")
                    return None

                try:
                    resp_json = await resp.json(content_type=None)
                except Exception as e:
                    text = await resp.text()
                    logger.error(f"[Platega] Не удалось распарсить JSON: {e}, ответ={text[:300]}")
                    return None

                payment_url = resp_json.get("redirect")
                transaction_id = str(resp_json.get("transactionId") or "")
                if not payment_url or not transaction_id:
                    logger.error(f"[Platega] В ответе нет redirect или transactionId: {resp_json}")
                    return None

                pending_metadata["platega_transaction_id"] = transaction_id
                pending_metadata["platega_order_id"] = unique_order_id

                async with async_session_maker() as db_session:
                    try:
                        await add_payment(
                            session=db_session,
                            tg_id=tg_id,
                            amount=float(int(amount)),
                            payment_system="platega",
                            status="pending",
                            currency="RUB",
                            payment_id=transaction_id,
                            metadata=pending_metadata,
                            original_amount=pending_original_amount,
                        )
                        await db_session.commit()
                    except Exception as e:
                        logger.error(
                            f"[Platega] Не удалось записать pending платёж в БД "
                            f"(transaction_id={transaction_id}, tg_id={tg_id}): {e}"
                        )
                        await db_session.rollback()
                        return None

                logger.info(
                    f"[Platega] Ссылка создана: tg_id={tg_id}, transaction_id={transaction_id}, "
                    f"order_id={unique_order_id}, rub_amount={amount}, "
                    f"api_amount={api_amount} {currency}, method={method_code}"
                )
                return payment_url
    except Exception as e:
        logger.error(f"[Platega] Ошибка создания платежа: {e}")
        return None


def _create_link_factory(method_name: str):
    async def create_link(
        session: AsyncSession,
        tg_id: int,
        amount: float,
        currency: str,
        success_url: str | None,
        failure_url: str | None,
        metadata: dict | None,
    ) -> tuple[str, str | None]:
        method = PLATEGA_METHODS.get(method_name)
        if not method or not _platega_method_enabled(method):
            raise ValueError("Способ оплаты Platega недоступен")

        amount_int = int(amount)
        if amount_int <= 0:
            raise ValueError("Сумма должна быть больше нуля")

        min_amount = PLATEGA_MIN_AMOUNTS.get(method_name, 10)
        if amount_int < min_amount:
            symbol = "$" if method["currency"] == "USD" else "₽"
            raise ValueError(f"Минимальная сумма Platega — {symbol}{min_amount}")

        payment_id = f"plg_{int(time.time())}_{tg_id}_{amount_int}"
        url = await generate_platega_payment_link(
            amount_int,
            tg_id,
            method,
            session,
            payment_id=payment_id,
            success_url=success_url,
            failure_url=failure_url,
            metadata=metadata,
        )
        if not url:
            raise ValueError("Не удалось создать платёж Platega")
        return (url, payment_id)

    return create_link


for _name in PLATEGA_METHODS:
    register_payment_creator(PLATEGA_METHODS[_name]["provider_key"], _create_link_factory(_name))
