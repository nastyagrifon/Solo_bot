import hashlib
import hmac
import json
import time

import aiohttp

from aiogram import Router
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession

from database import register_pending_payment
from handlers.payments.topup_flow import register_topup_flow
from logger import logger
from services.payments.payment_links import register_payment_creator
from settings.buttons import PARITYPAY_SBP
from settings.config import (
    PARITYPAY_API_SECRET_KEY,
    PARITYPAY_API_URL,
    PARITYPAY_CALLBACK_URL,
    PARITYPAY_FAIL_URL,
    PARITYPAY_SHOP_ID,
    PARITYPAY_SUCCESS_URL,
    PROVIDERS_ENABLED,
)
from settings.texts import (
    PARITYPAY_PAYMENT_MESSAGE,
    PARITYPAY_SBP_DESCRIPTION,
)


router = Router()


class ReplenishBalanceParityPay(StatesGroup):
    choosing_method = State()
    choosing_amount = State()
    waiting_for_payment_confirmation = State()
    entering_custom_amount = State()


PARITYPAY_METHODS = {
    "sbp": {
        "enable": PROVIDERS_ENABLED.get("PARITYPAY_SBP", False),
        "service": "sbp",
        "button": PARITYPAY_SBP,
        "desc": PARITYPAY_SBP_DESCRIPTION,
        "min_amount": 10,
    },
}


def _build_signature_string(payload: dict) -> str:
    parts: list[str] = []
    for key in sorted(payload.keys()):
        value = payload[key]
        if value is None:
            parts.append("")
        elif isinstance(value, bool):
            parts.append("1" if value else "0")
        else:
            parts.append(str(value))
    return "".join(parts)


def _sign_request(payload: dict) -> str:
    sign_string = _build_signature_string(payload)
    return hmac.new(
        PARITYPAY_API_SECRET_KEY.encode("utf-8"),
        sign_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


process_callback_pay_paritypay = register_topup_flow(
    router,
    prefix="paritypay",
    methods=PARITYPAY_METHODS,
    states=ReplenishBalanceParityPay,
    enabled=lambda method: method["enable"],
    payment_link=lambda amount, tg_id, method, session: generate_paritypay_payment_link(amount, tg_id, method, session),
    payment_message=PARITYPAY_PAYMENT_MESSAGE,
    min_amount=lambda name, method: method["min_amount"],
    input_min_text=lambda name: "❌ Минимальная сумма для оплаты — {min}₽.",
    amount_min_text=lambda name: "❌ Минимальная сумма для оплаты — {min}₽.",
    entry_error_log=lambda cq, e: f"Error in process_callback_pay_paritypay for user {cq.from_user.id}: {e}",
    multicurrency_input=False,
    link_by_chat_id=False,
    custom_back_cb="pay_paritypay",
    entry_log="User {tg_id} initiated ParityPay payment.",
    menu_text="Выберите способ оплаты ParityPay:",
    method_back_cb="pay",
)


async def generate_paritypay_payment_link(
    amount: int,
    tg_id: int,
    method: dict,
    session: AsyncSession | None = None,
    *,
    order_id: str | None = None,
    success_url: str | None = None,
    fail_url: str | None = None,
    metadata: dict | None = None,
) -> str | None:
    unique_order_id = order_id or f"{int(time.time())}_{tg_id}"
    payload = {
        "shop_id": PARITYPAY_SHOP_ID,
        "amount": int(amount),
        "order_id": unique_order_id,
        "service": method["service"],
        "success_url": success_url or PARITYPAY_SUCCESS_URL or "",
        "fail_url": fail_url or PARITYPAY_FAIL_URL or "",
        "callback_url": PARITYPAY_CALLBACK_URL or "",
        "user_hash": str(tg_id),
    }
    payload = {k: v for k, v in payload.items() if v not in (None, "")}
    signature = _sign_request(payload)
    headers = {"Content-Type": "application/json", "X-SIGNATURE": signature}
    url = f"{PARITYPAY_API_URL.rstrip('/')}/invoice/create"

    timeout = aiohttp.ClientTimeout(total=30, connect=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http_session:
            async with http_session.post(url, headers=headers, data=json.dumps(payload)) as resp:
                text = await resp.text()
                if resp.status != 200:
                    logger.error(f"ParityPay API error: status={resp.status}, body={text}")
                    return None
                try:
                    resp_json = json.loads(text)
                except Exception as e:
                    logger.error(f"ParityPay: невалидный JSON в ответе ({e}): {text}")
                    return None
                if "error" in resp_json:
                    logger.error(f"ParityPay error: {resp_json}")
                    return None
                payment_url = resp_json.get("link")
                if not payment_url:
                    logger.error(f"ParityPay: пустая ссылка в ответе: {resp_json}")
                    return None
                await register_pending_payment(
                    payment_id=unique_order_id,
                    tg_id=tg_id,
                    amount=float(amount),
                    payment_system="paritypay",
                    currency="RUB",
                    metadata=metadata,
                )
                logger.info(f"ParityPay payment URL created for user {tg_id}, order_id={unique_order_id}")
                return payment_url
    except Exception as e:
        logger.error(f"Error creating ParityPay payment: {e}")
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
        if currency != "RUB":
            raise ValueError("ParityPay поддерживает только RUB")
        method = PARITYPAY_METHODS.get(method_name)
        if not method or not method.get("enable"):
            raise ValueError("Способ оплаты ParityPay недоступен")
        amount_int = int(amount)
        if amount_int < method["min_amount"]:
            raise ValueError(f"Минимальная сумма — {method['min_amount']}₽")
        order_id = f"{int(time.time())}_{tg_id}"
        url = await generate_paritypay_payment_link(
            amount_int,
            tg_id,
            method,
            session,
            order_id=order_id,
            success_url=success_url,
            fail_url=failure_url,
            metadata=metadata,
        )
        if not url:
            raise ValueError("Не удалось создать платёж ParityPay")
        return (url, order_id)

    return create_link


register_payment_creator("PARITYPAY_SBP", _create_link_factory("sbp"))
