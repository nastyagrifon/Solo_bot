from collections.abc import Iterable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.tariffs import get_active_tariffs_by_group_code
from logger import logger
from services.payments.currency_rates import format_for_user
from settings.buttons import BACK, CUSTOM_AMOUNT
from settings.config import RENEWAL_PRICES

#: Группа тарифов, цены которой предлагаются при пополнении баланса.
TOPUP_TARIFF_GROUP = "basic"


async def _topup_amounts(db_session) -> list[int]:
    """Суммы кнопок пополнения: базовые цены тарифов, по возрастанию срока.

    Берём `price_rub` активных тарифов группы `basic` - это цена за ОДНО
    устройство (решение Насти 15.10.2026). До этого кнопки брались из
    `RENEWAL_PRICES`, захардкоженного в settings/config.py: 100/285/540/1000.
    Числа остались от старой сетки, и после подорожания 27.09.2026 клиент
    видел 100 рублей там, где месяц стоит 229 - ни одна кнопка, кроме 1000,
    не покрывала даже один период.

    Тарифы конфигурируемые: за каждое устройство сверх первого цена растёт,
    поэтому суммы здесь - минимальные, а не итоговые для клиента с тремя
    устройствами.

    При пустой группе или сбое базы возвращаем прежний список: без кнопок
    пополнения клиент остаётся вообще без способа заплатить, и это хуже
    неактуальных сумм.
    """
    try:
        tariffs = await get_active_tariffs_by_group_code(db_session, TOPUP_TARIFF_GROUP)
    except Exception as exc:
        logger.warning(f"[Topup] Тарифы не прочитались, беру RENEWAL_PRICES: {exc}")
        return [int(p) for p in RENEWAL_PRICES.values()]
    amounts = [
        int(t.price_rub)
        for t in sorted(tariffs, key=lambda x: (x.duration_days or 0))
        if t.price_rub and int(t.price_rub) > 0
    ]
    if not amounts:
        logger.warning("[Topup] В группе %s нет платных тарифов, беру RENEWAL_PRICES" % TOPUP_TARIFF_GROUP)
        return [int(p) for p in RENEWAL_PRICES.values()]
    return amounts


async def payment_options_for_user(
    db_session,
    tg_id: int,
    language_code: str | None,
    *,
    force_currency: str | None = None,
) -> list[dict]:
    items = []
    for price_rub in await _topup_amounts(db_session):
        txt = await format_for_user(
            db_session,
            tg_id,
            price_rub,
            language_code,
            force_currency=force_currency,
        )
        items.append({"text": txt, "callback_data": f"amount|{int(price_rub)}"})
    return items


def payment_options(currency: str = "RUB") -> list[dict]:
    return [{"text": f"{price} {currency}", "callback_data": f"amount|{price}"} for price in RENEWAL_PRICES.values()]


def build_amounts_keyboard(
    *,
    prefix: str,
    pattern: str,
    back_cb: str = "balance",
    custom_cb: str | tuple[str, str] | None = None,
    per_row: int = 2,
    opts: Iterable[dict] | None = None,
) -> InlineKeyboardMarkup:
    items = list(opts) if opts is not None else payment_options()
    b = InlineKeyboardBuilder()
    row = []
    for i, item in enumerate(items, 1):
        row.append(
            InlineKeyboardButton(
                text=item["text"],
                callback_data=pattern.format(prefix=prefix, price=item["callback_data"].split("|", 1)[-1]),
            )
        )
        if i % per_row == 0:
            b.row(*row)
            row = []
    if row:
        b.row(*row)
    if custom_cb:
        cb = custom_cb[1] if isinstance(custom_cb, tuple) else custom_cb
        b.row(InlineKeyboardButton(text=CUSTOM_AMOUNT, callback_data=cb))
    b.row(InlineKeyboardButton(text=BACK, callback_data=back_cb))
    return b.as_markup()


def parse_amount_from_callback(data: str, *, prefixes: list[str]) -> int | None:
    for p in prefixes:
        if data.startswith(f"{p}_amount|"):
            try:
                return int(data.split("|", 1)[1])
            except Exception:
                return None
        if data.startswith(f"{p}|amount|"):
            try:
                return int(data.rsplit("|", 1)[-1])
            except Exception:
                return None
        if data.startswith(f"{p}|"):
            try:
                return int(data.split("|", 1)[1])
            except Exception:
                return None
    return None


def pay_keyboard(url: str, *, pay_text: str, back_cb: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text=pay_text, url=url))
    b.row(InlineKeyboardButton(text=BACK, callback_data=back_cb))
    return b.as_markup()


def back_keyboard(back_cb: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text=BACK, callback_data=back_cb))
    return b.as_markup()


def balance_fallback_kb() -> InlineKeyboardMarkup:
    from settings.buttons import BALANCE

    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text=BALANCE, callback_data="balance"))
    return b.as_markup()
