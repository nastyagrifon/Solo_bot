import base64
import hashlib
import json
import time

from decimal import ROUND_HALF_UP, Decimal

import aiohttp

from aiogram import Router
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import PAYMENTS_CONFIG
from database import register_pending_payment
from handlers.payments.topup_flow import register_topup_flow
from logger import logger
from services.payments.currency_rates import get_rub_rate
from services.payments.payment_links import register_payment_creator
from settings.buttons import HELEKET
from settings.config import (
    HELEKET_API_KEY,
    HELEKET_CALLBACK_URL,
    HELEKET_MERCHANT_ID,
    HELEKET_RETURN_URL,
    HELEKET_SUCCESS_URL,
)
from settings.texts import (
    HELEKET_CRYPTO_DESCRIPTION,
    HELEKET_PAYMENT_MESSAGE,
)


router = Router()


class ReplenishBalanceHeleket(StatesGroup):
    choosing_method = State()
    choosing_amount = State()
    waiting_for_payment_confirmation = State()
    entering_custom_amount = State()


HELEKET_METHODS = {
    "crypto": {
        "provider_key": "HELEKET",
        "currency": "USD",
        "to_currency": None,
        "button": HELEKET,
        "desc": HELEKET_CRYPTO_DESCRIPTION,
    },
}


def _heleket_method_enabled(method: dict) -> bool:
    return bool(PAYMENTS_CONFIG.get(method["provider_key"], False))


async def _payment_link(amount: int, tg_id: int, method: dict, session: AsyncSession) -> str | None:
    payment_url = await generate_heleket_payment_link(amount, tg_id, method, session)
    if not payment_url or payment_url == "https://heleket.com/":
        return None
    return payment_url


process_callback_pay_heleket = register_topup_flow(
    router,
    prefix="heleket",
    methods=HELEKET_METHODS,
    states=ReplenishBalanceHeleket,
    enabled=_heleket_method_enabled,
    payment_link=_payment_link,
    payment_message=HELEKET_PAYMENT_MESSAGE,
    min_amount=lambda name, method: 10,
    input_min_text=lambda name: "❌ Минимальная сумма для оплаты криптовалютой — {symbol}{min}.",
    amount_min_text=lambda name: "❌ Минимальная сумма для оплаты криптовалютой — 10₽ (≈0.1$).",
    entry_error_log=lambda cq, e: f"Error in process_callback_pay_heleket for user {cq.message.chat.id}: {e}",
    multicurrency_input=True,
    link_by_chat_id=True,
    custom_back_cb="pay_heleket_crypto",
    entry_log="User {tg_id} initiated Heleket payment.",
    menu_text="Выберите способ оплаты через Heleket:",
    method_back_cb="pay",
    autopick_single=True,
)


async def generate_heleket_payment_link(
    amount: int,
    tg_id: int,
    method: dict,
    session: AsyncSession | None = None,
    *,
    order_id: str | None = None,
    success_url: str | None = None,
    failure_url: str | None = None,
    metadata: dict | None = None,
) -> str:
    """
    Создание платежа в Heleket и получение ссылки на оплату.
    amount — сумма в RUB, method['currency'] — валюта провайдера (обычно USD).
    session — сессия из хендлера; если не передана, создаётся своя (лишняя нагрузка на пул).
    """
    url = "https://api.heleket.com/v1/payment"
    unique_order_id = order_id or f"{int(time.time())}_{tg_id}"

    timeout = aiohttp.ClientTimeout(total=30, connect=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http_session:
            pay_cur = str(method["currency"]).upper()

            if pay_cur == "RUB":
                payment_amount = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            else:
                rate = await get_rub_rate(pay_cur, session=http_session)
                payment_amount = (Decimal(str(amount)) * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            data = {
                "amount": str(payment_amount),
                "currency": method["currency"],
                "order_id": unique_order_id,
                "url_success": success_url or HELEKET_SUCCESS_URL,
                "url_return": failure_url or HELEKET_RETURN_URL,
                "url_callback": HELEKET_CALLBACK_URL,
                "additional_data": f"tg_id:{tg_id},rub_amount:{amount}",
            }
            if method.get("to_currency"):
                data["to_currency"] = method["to_currency"]

            json_data = json.dumps(data, separators=(",", ":"))
            base64_data = base64.b64encode(json_data.encode("utf-8")).decode("utf-8")
            sign_string = base64_data + HELEKET_API_KEY
            signature = hashlib.md5(sign_string.encode("utf-8")).hexdigest()

            headers = {
                "merchant": HELEKET_MERCHANT_ID,
                "sign": signature,
                "Content-Type": "application/json",
            }

            async with http_session.post(url, headers=headers, data=json_data, timeout=60) as resp:
                if resp.status == 200:
                    try:
                        resp_json = await resp.json()
                        if resp_json.get("state") == 0:
                            payment_url = resp_json.get("result", {}).get("url")
                            if payment_url:
                                await register_pending_payment(
                                    payment_id=unique_order_id,
                                    tg_id=tg_id,
                                    amount=float(amount),
                                    payment_system="heleket",
                                    currency="RUB",
                                    metadata=metadata,
                                )
                                logger.info(f"Heleket payment URL created for user {tg_id}")
                                return payment_url
                            else:
                                logger.error(f"Heleket: No URL in response: {resp_json}")
                                return "https://heleket.com/"
                        else:
                            logger.error(f"Heleket: Unsuccessful response: {resp_json}")
                            return "https://heleket.com/"
                    except Exception as e:
                        logger.error(f"Heleket: Error parsing JSON response: {e}")
                        text = await resp.text()
                        logger.error(f"Heleket: Response content: {text}")
                        return "https://heleket.com/"
                else:
                    try:
                        error_json = await resp.json()
                        logger.error(f"Heleket API error: status={resp.status}, response={error_json}")
                    except Exception:
                        text = await resp.text()
                        logger.error(f"Heleket API error: status={resp.status}, non-JSON response: {text}")
                    return "https://heleket.com/"
    except Exception as e:
        logger.error(f"Error creating Heleket payment: {e}")
        return "https://heleket.com/"


async def create_link(
    session: AsyncSession,
    tg_id: int,
    amount: float,
    currency: str,
    success_url: str | None,
    failure_url: str | None,
    metadata: dict | None,
) -> tuple[str, str | None]:
    method = HELEKET_METHODS.get("crypto")
    if not method or not _heleket_method_enabled(method):
        raise ValueError("Heleket недоступен")
    amount_int = int(amount)
    order_id = f"{int(time.time())}_{tg_id}"
    if amount_int < 10:
        raise ValueError("Минимальная сумма для Heleket — 10₽")
    url = await generate_heleket_payment_link(
        amount_int,
        tg_id,
        method,
        session,
        order_id=order_id,
        success_url=success_url,
        failure_url=failure_url,
        metadata=metadata,
    )
    if not url or url == "https://heleket.com/":
        raise ValueError("Не удалось создать платёж Heleket")
    return (url, order_id)


register_payment_creator("HELEKET", create_link)
