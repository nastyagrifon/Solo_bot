import json
import time
import uuid

from urllib.parse import urlsplit

import aiohttp

from aiogram import Router
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import PAYMENTS_CONFIG
from database import register_pending_payment
from database.access.resolution import TelegramId
from database.payments import resolve_payment_creation_owner
from handlers.payments.topup_flow import register_topup_flow
from logger import logger
from services.payments.checkout_snapshot import capture_payment_checkout
from services.payments.owner_refs import payment_owner_token
from services.payments.payment_links import register_payment_creator
from settings.buttons import PARITYPAY_SBP
from settings.config import (
    PARITYPAY_API_SECRET_KEY,
    PARITYPAY_API_URL,
    PARITYPAY_CALLBACK_URL,
    PARITYPAY_FAIL_URL,
    PARITYPAY_SHOP_ID,
    PARITYPAY_SUCCESS_URL,
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
        "provider_key": "PARITYPAY_SBP",
        "service": "sbp",
        "button": PARITYPAY_SBP,
        "desc": PARITYPAY_SBP_DESCRIPTION,
        "min_amount": 10,
    },
}


def _paritypay_method_enabled(method: dict) -> bool:
    return bool(PAYMENTS_CONFIG.get(method["provider_key"], False))


def _new_paritypay_order_id(tg_id: int) -> str:
    return f"{int(time.time())}_{payment_owner_token(tg_id)}_{uuid.uuid4().hex}"


def _paritypay_invoice_create_url() -> str:
    configured_url = (PARITYPAY_API_URL or "").strip().rstrip("/")
    configured_host = (urlsplit(configured_url).hostname or "").casefold()
    if configured_host in {"api.paritypay.ru", "api.paritypay.net"}:
        return "https://api.paritypay.net/v2/invoice/create"
    if not configured_url:
        configured_url = "https://api.paritypay.net"
    return f"{configured_url}/v2/invoice/create"


process_callback_pay_paritypay = register_topup_flow(
    router,
    prefix="paritypay",
    methods=PARITYPAY_METHODS,
    states=ReplenishBalanceParityPay,
    enabled=_paritypay_method_enabled,
    payment_link=lambda amount, tg_id, method, session: generate_paritypay_payment_link(
        amount, TelegramId(tg_id), method, session
    ),
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
    tg_id = await resolve_payment_creation_owner(session, tg_id)
    metadata = await capture_payment_checkout(session, tg_id, metadata)
    unique_order_id = order_id or _new_paritypay_order_id(tg_id)
    payload = {
        "amount": int(amount),
        "order_id": unique_order_id,
        "service": method["service"],
        "success_url": success_url or PARITYPAY_SUCCESS_URL or "",
        "fail_url": fail_url or PARITYPAY_FAIL_URL or "",
        "callback_url": PARITYPAY_CALLBACK_URL or "",
    }
    payload = {k: v for k, v in payload.items() if v not in (None, "")}
    headers = {
        "Content-Type": "application/json",
        "X-ShopId": PARITYPAY_SHOP_ID,
        "X-SecretKey": PARITYPAY_API_SECRET_KEY,
    }
    url = _paritypay_invoice_create_url()

    timeout = aiohttp.ClientTimeout(total=30, connect=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http_session:
            async with http_session.post(url, headers=headers, json=payload) as resp:
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
        tg_id = await resolve_payment_creation_owner(session, tg_id)
        if currency != "RUB":
            raise ValueError("ParityPay поддерживает только RUB")
        method = PARITYPAY_METHODS.get(method_name)
        if not method or not _paritypay_method_enabled(method):
            raise ValueError("Способ оплаты ParityPay недоступен")
        amount_int = int(amount)
        if amount_int < method["min_amount"]:
            raise ValueError(f"Минимальная сумма — {method['min_amount']}₽")
        order_id = _new_paritypay_order_id(tg_id)
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
