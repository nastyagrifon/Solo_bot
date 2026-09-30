import hashlib
import hmac
import time

import aiohttp

from aiogram import Router
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import PAYMENTS_CONFIG
from database import register_pending_payment
from handlers.payments.topup_flow import register_topup_flow
from logger import logger
from services.payments.payment_links import register_payment_creator
from settings.buttons import KASSAI_CARDS, KASSAI_SBP
from settings.config import (
    KASSAI_API_KEY,
    KASSAI_DOMAIN,
    KASSAI_FAILURE_URL,
    KASSAI_IP,
    KASSAI_SHOP_ID,
    KASSAI_SUCCESS_URL,
)
from settings.texts import (
    KASSAI_CARDS_DESCRIPTION,
    KASSAI_PAYMENT_MESSAGE,
    KASSAI_SBP_DESCRIPTION,
)


router = Router()


class ReplenishBalanceKassaiState(StatesGroup):
    """Состояния FSM для пополнения баланса через KassaI."""

    choosing_method = State()
    choosing_amount = State()
    waiting_for_payment_confirmation = State()
    entering_custom_amount = State()


KASSAI_METHODS = {
    "cards": {
        "provider_key": "KASSAI_CARDS",
        "method": 36,
        "button": KASSAI_CARDS,
        "desc": KASSAI_CARDS_DESCRIPTION,
    },
    "sbp": {
        "provider_key": "KASSAI_SBP",
        "method": 44,
        "button": KASSAI_SBP,
        "desc": KASSAI_SBP_DESCRIPTION,
    },
}


def _kassai_method_enabled(method: dict) -> bool:
    return bool(PAYMENTS_CONFIG.get(method["provider_key"], False))


KASSAI_MIN_AMOUNTS = {"cards": 50, "sbp": 10}
KASSAI_MIN_TEXTS = {"cards": "оплаты картой", "sbp": "оплаты через СБП"}


async def _payment_link(amount: int, tg_id: int, method: dict, session: AsyncSession) -> str | None:
    payment_url = await generate_kassai_payment_link(amount, tg_id, method, session)
    if not payment_url or payment_url == "https://fk.life/":
        return None
    return payment_url


process_callback_pay_kassai = register_topup_flow(
    router,
    prefix="kassai",
    methods=KASSAI_METHODS,
    states=ReplenishBalanceKassaiState,
    enabled=_kassai_method_enabled,
    payment_link=_payment_link,
    payment_message=KASSAI_PAYMENT_MESSAGE,
    min_amount=lambda name, method: KASSAI_MIN_AMOUNTS[name],
    input_min_text=lambda name: f"❌ Минимальная сумма для {KASSAI_MIN_TEXTS[name]} — {{symbol}}{{min}}.",
    amount_min_text=lambda name: f"❌ Минимальная сумма для {KASSAI_MIN_TEXTS[name]} — {{min}}₽.",
    entry_error_log=lambda cq, e: f"Error in process_callback_pay_kassai for user {cq.message.chat.id}: {e}",
    multicurrency_input=True,
    link_by_chat_id=True,
    custom_back_cb="pay_kassai_{method}",
    entry_log="User {tg_id} initiated KassaAI payment.",
    menu_text="Выберите способ оплаты через KassaAI:",
    method_back_cb="pay_kassai",
    entry_cb="pay_kassai",
)


async def generate_kassai_payment_link(
    amount: int,
    tg_id: int,
    method: dict,
    session: AsyncSession | None = None,
    *,
    payment_id: str | None = None,
    success_url: str | None = None,
    failure_url: str | None = None,
    metadata: dict | None = None,
) -> str:
    """
    Создание заказа в KassaAI и получение ссылки на оплату.
    session — сессия из хендлера; если не передана, создаётся своя (лишняя нагрузка на пул).
    """
    nonce = int(time.time())
    unique_payment_id = payment_id or f"{nonce}_{tg_id}"
    url = "https://api.fk.life/v1/orders/create"

    headers = {"Content-Type": "application/json"}

    client_email = f"{tg_id}@{KASSAI_DOMAIN}"
    client_ip = KASSAI_IP

    data_for_signature = {
        "shopId": KASSAI_SHOP_ID,
        "nonce": nonce,
        "i": method["method"],
        "email": client_email,
        "ip": client_ip,
        "amount": int(amount),
        "currency": "RUB",
        "success_url": success_url or KASSAI_SUCCESS_URL,
        "failure_url": failure_url or KASSAI_FAILURE_URL,
        "paymentId": unique_payment_id,
    }

    sign_string = "|".join(str(data_for_signature[k]) for k in sorted(data_for_signature.keys()))
    signature = hmac.new(KASSAI_API_KEY.encode("utf-8"), sign_string.encode("utf-8"), hashlib.sha256).hexdigest()

    data = {**data_for_signature, "signature": signature}

    timeout = aiohttp.ClientTimeout(total=60, connect=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http_session:
            async with http_session.post(url, headers=headers, json=data, timeout=60) as resp:
                if resp.status == 200:
                    try:
                        resp_json = await resp.json()
                        if resp_json.get("type") == "success":
                            payment_url = resp_json.get("location")
                            if payment_url:
                                await register_pending_payment(
                                    payment_id=unique_payment_id,
                                    tg_id=tg_id,
                                    amount=float(amount),
                                    payment_system="kassai",
                                    currency="RUB",
                                    metadata=metadata,
                                )
                                logger.info(f"KassaAI payment URL created for user {tg_id}")
                                return payment_url
                            logger.error(f"KassaAI: No location in response: {resp_json}")
                            return "https://fk.life/"
                        logger.error(f"KassaAI: Unsuccessful response: {resp_json}")
                        return "https://fk.life/"
                    except Exception as e:
                        logger.error(f"KassaAI: Error parsing JSON response: {e}")
                        text = await resp.text()
                        logger.error(f"KassaAI: Response content: {text}")
                        return "https://fk.life/"
                else:
                    try:
                        error_json = await resp.json()
                        logger.error(f"KassaAI API error: status={resp.status}, response={error_json}")
                    except Exception:
                        text = await resp.text()
                        logger.error(f"KassaAI API error: status={resp.status}, non-JSON response: {text}")
                    return "https://fk.life/"
    except Exception as e:
        logger.error(f"Error creating KassaAI order: {e}")
        return "https://fk.life/"


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
        if currency != "RUB":
            raise ValueError("KassaI поддерживает только RUB")
        method = KASSAI_METHODS.get(method_name)
        if not method or not _kassai_method_enabled(method):
            raise ValueError("Способ оплаты KassaI недоступен")
        amount_int = int(amount)
        payment_id = f"{int(time.time())}_{tg_id}"
        if method_name == "cards" and amount_int < 50:
            raise ValueError("Минимальная сумма для карт — 50₽")
        if method_name == "sbp" and amount_int < 10:
            raise ValueError("Минимальная сумма для СБП — 10₽")
        url = await generate_kassai_payment_link(
            amount_int,
            tg_id,
            method,
            session,
            payment_id=payment_id,
            success_url=success_url,
            failure_url=failure_url,
            metadata=metadata,
        )
        if not url or url == "https://fk.life/":
            raise ValueError("Не удалось создать платёж KassaI")
        return (url, payment_id)

    return create_link


register_payment_creator("KASSAI_CARDS", create_link_factory("cards"))
register_payment_creator("KASSAI_SBP", create_link_factory("sbp"))
