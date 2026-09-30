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
from settings.buttons import WATA_INT, WATA_RU
from settings.config import (
    WATA_FAIL_URL,
    WATA_INT_TOKEN,
    WATA_RU_TOKEN,
    WATA_SUCCESS_URL,
)
from settings.texts import (
    WATA_INT_DESCRIPTION,
    WATA_PAYMENT_MESSAGE,
    WATA_PAYMENT_TITLE,
    WATA_RU_DESCRIPTION,
)


router = Router()


WATA_API_LINKS_URL = "https://api.wata.pro/api/h2h/links"

WATA_MIN_AMOUNTS = {
    "ru": 10,
    "int": 1,
}


class ReplenishBalanceWata(StatesGroup):
    choosing_method = State()
    choosing_amount = State()
    waiting_for_payment_confirmation = State()
    entering_custom_amount = State()


WATA_METHODS = {
    "ru": {
        "provider_key": "WATA_RU",
        "currency": "RUB",
        "token": WATA_RU_TOKEN,
        "button": WATA_RU,
        "desc": WATA_RU_DESCRIPTION,
    },
    "int": {
        "provider_key": "WATA_INT",
        "currency": "USD",
        "token": WATA_INT_TOKEN,
        "button": WATA_INT,
        "desc": WATA_INT_DESCRIPTION,
    },
}


def _wata_method_enabled(method: dict) -> bool:
    return bool(PAYMENTS_CONFIG.get(method["provider_key"], False))


process_callback_pay_wata = register_topup_flow(
    router,
    prefix="wata",
    methods=WATA_METHODS,
    states=ReplenishBalanceWata,
    enabled=_wata_method_enabled,
    payment_link=lambda amount, tg_id, method, session: generate_wata_payment_link(amount, tg_id, method, session),
    payment_message=WATA_PAYMENT_MESSAGE,
    min_amount=lambda name, method: WATA_MIN_AMOUNTS.get(name, 10),
    input_min_text=lambda name: "❌ Минимальная сумма для оплаты через WATA — {symbol}{min}.",
    amount_min_text=lambda name: "❌ Минимальная сумма для оплаты через WATA — {symbol}{min}.",
    entry_error_log=lambda cq, e: f"Error in process_callback_pay_wata for user {cq.message.chat.id}: {e}",
    multicurrency_input=True,
    link_by_chat_id=True,
    custom_back_cb="pay_wata_{method}",
    entry_log="User {tg_id} initiated Wata payment.",
    menu_text="Выберите способ оплаты через WATA:",
    method_back_cb="pay_wata",
    entry_cb="pay_wata",
)


async def generate_wata_payment_link(
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
    token = method.get("token") or ""
    if not token:
        logger.error(f"[WATA] Не задан токен для кассы {method.get('currency')}")
        return None

    currency = str(method.get("currency") or "RUB").upper()
    unique_order_id = payment_id or f"{int(time.time())}_{tg_id}_{int(amount)}"

    pending_metadata = dict(metadata or {})
    pending_metadata.setdefault("cassa", "ru" if currency == "RUB" else "int")

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
            pending_metadata["wata_currency"] = currency
        except Exception as e:
            logger.error(f"[WATA] Не удалось сконвертировать {amount} RUB → {currency}: {e}")
            return None

    body = {
        "amount": api_amount,
        "currency": currency,
        "orderId": unique_order_id,
        "orderDescription": WATA_PAYMENT_TITLE,
        "successUrl": success_url or WATA_SUCCESS_URL or "",
        "failUrl": failure_url or WATA_FAIL_URL or "",
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    timeout = aiohttp.ClientTimeout(total=60, connect=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http_session:
            async with http_session.post(WATA_API_LINKS_URL, headers=headers, json=body) as resp:
                if resp.status != 200:
                    try:
                        error_json = await resp.json()
                        logger.error(f"[WATA] API error: status={resp.status}, response={error_json}")
                    except Exception:
                        text = await resp.text()
                        logger.error(f"[WATA] API error: status={resp.status}, non-JSON: {text[:300]}")
                    return None

                try:
                    resp_json = await resp.json()
                except Exception as e:
                    text = await resp.text()
                    logger.error(f"[WATA] Не удалось распарсить JSON: {e}, ответ={text[:300]}")
                    return None

                payment_url = resp_json.get("url")
                if not payment_url:
                    logger.error(f"[WATA] В ответе нет поля url: {resp_json}")
                    return None

                async with async_session_maker() as db_session:
                    try:
                        await add_payment(
                            session=db_session,
                            tg_id=tg_id,
                            amount=float(int(amount)),
                            payment_system="wata",
                            status="pending",
                            currency="RUB",
                            payment_id=unique_order_id,
                            metadata=pending_metadata,
                            original_amount=pending_original_amount,
                        )
                        await db_session.commit()
                    except Exception as e:
                        logger.error(
                            f"[WATA] Не удалось записать pending платёж в БД "
                            f"(order_id={unique_order_id}, tg_id={tg_id}): {e}"
                        )
                        await db_session.rollback()
                        return None
                logger.info(
                    f"[WATA] Ссылка создана: tg_id={tg_id}, order_id={unique_order_id}, "
                    f"rub_amount={amount}, api_amount={api_amount} {currency}"
                )
                return payment_url
    except Exception as e:
        logger.error(f"[WATA] Ошибка создания платежа: {e}")
        return None


def create_link_factory(method_name: str):
    async def create_link(
        session: AsyncSession,
        tg_id: int,
        amount: float,
        currency: str,
        success_url: str | None,
        failure_url: str | None,
        metadata: dict | None,
    ) -> tuple[str, str | None]:
        method = WATA_METHODS.get(method_name)
        if not method or not _wata_method_enabled(method):
            raise ValueError("Способ оплаты Wata недоступен")

        amount_int = int(amount)
        if amount_int <= 0:
            raise ValueError("Сумма должна быть больше нуля")

        min_amount = WATA_MIN_AMOUNTS.get(method_name, 10)
        if amount_int < min_amount:
            symbol = "$" if method["currency"] == "USD" else "₽"
            raise ValueError(f"Минимальная сумма Wata — {symbol}{min_amount}")

        payment_id = f"{int(time.time())}_{tg_id}_{amount_int}"
        url = await generate_wata_payment_link(
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
            raise ValueError("Не удалось создать платёж Wata")
        return (url, payment_id)

    return create_link


register_payment_creator("WATA_RU", create_link_factory("ru"))
register_payment_creator("WATA_INT", create_link_factory("int"))
