from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.settings.tariffs_config import normalize_tariff_config
from database.models import Tariff
from filters.admin import IsAdminFilter

from ...panel.headers import card, menu_text, quote, section
from .. import router
from ..keyboard import AdminTariffCallback


class TariffConfigState(StatesGroup):
    choosing_section = State()
    entering_devices = State()
    entering_traffic = State()
    entering_device_step = State()
    entering_device_overrides = State()
    entering_traffic_step = State()
    entering_traffic_overrides = State()


def build_config_menu_kb(tariff_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📱 Варианты устройств",
                    callback_data=f"cfg_edit_devices|{tariff_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📦 Варианты трафика",
                    callback_data=f"cfg_edit_traffic|{tariff_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💰 Шаг за устройство",
                    callback_data=f"cfg_edit_device_step|{tariff_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Доплаты за устройства",
                    callback_data=f"cfg_edit_device_over|{tariff_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💰 Шаг за 1 ГБ",
                    callback_data=f"cfg_edit_traffic_step|{tariff_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Доплаты за трафик",
                    callback_data=f"cfg_edit_traffic_over|{tariff_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к тарифу",
                    callback_data=AdminTariffCallback(action=f"view|{tariff_id}").pack(),
                )
            ],
        ]
    )


def build_cancel_config_kb(tariff_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"edit_config|{tariff_id}",
                )
            ]
        ]
    )


def calculate_device_formula_extra(tariff: Tariff, devices: int) -> int:
    base_devices = tariff.device_limit
    step = getattr(tariff, "device_step_rub", None) or 0
    if base_devices is None or devices <= base_devices:
        return 0
    return (devices - base_devices) * step


def calculate_traffic_formula_extra(tariff: Tariff, gb_value: int) -> int:
    base_traffic = tariff.traffic_limit
    step = getattr(tariff, "traffic_step_rub", None) or 0
    if gb_value == 0:
        return 0
    if base_traffic is None or gb_value <= base_traffic:
        return 0
    return (gb_value - base_traffic) * step


# Устройства и трафик устроены одинаково: всё, чем они различаются, — в этой таблице.
KIND = {
    "dev": {
        "options_field": "device_options",
        "step_field": "device_step_rub",
        "overrides_field": "device_overrides",
        "formula": calculate_device_formula_extra,
        # Устройства: варианты как есть. Трафик: безлимит (0) в списке всегда.
        "screen_options": sorted,
        "screen_title": "Доплаты за устройства",
        "screen_zero_hint": "<code>0</code> среди вариантов — безлимит по устройствам.",
        "short_zero": "безлимит устройств",
        "short": "{} устр.",
        "long_zero": "безлимитное количество устройств",
        "long": "{} устройств",
        "item_cb": "cfg_dev_over_item",
        "clear_cb": "cfg_dev_over_clear",
        "step_cb": "cfg_edit_device_step",
        "over_cb": "cfg_edit_device_over",
        "step_state": TariffConfigState.entering_device_step,
        "over_state": TariffConfigState.entering_device_overrides,
        "step_title": "Шаг за устройство",
        "step_hint": "Цена в рублях за каждое устройство сверх базового лимита.",
        "step_example": "50",
        "step_off": "<code>0</code> выключает автодоплату за устройства.",
        "data_key": "devices_override",
        "pick_first": "Сначала выберите вариант устройств из списка.",
        # Без вариантов устройств экран доплат не открывается; у трафика есть безлимит всегда.
        "need_options": (
            "Доплаты за устройства",
            "Сначала задайте варианты устройств.",
            "Кнопка «Варианты устройств» в конфигураторе.",
        ),
        "ignore_not_modified": False,
        "options_cb": "cfg_edit_devices",
        "options_state": TariffConfigState.entering_devices,
        "options_title": "Варианты устройств",
        "options_ask": "Пришлите список через пробел или запятую.",
        "options_example": "1 3 5",
        "options_off": "Один только <code>0</code> — выбор устройств отключён, остаётся базовый лимит тарифа.",
    },
    "trf": {
        "options_field": "traffic_options_gb",
        "step_field": "traffic_step_rub",
        "overrides_field": "traffic_overrides",
        "formula": calculate_traffic_formula_extra,
        "screen_options": lambda options: sorted(set(options + [0])),
        "screen_title": "Доплаты за трафик",
        "screen_zero_hint": "<code>0</code> среди вариантов — безлимитный трафик.",
        "short_zero": "безлимит",
        "short": "{} ГБ",
        "long_zero": "безлимитный трафик",
        "long": "лимит {} ГБ",
        "item_cb": "cfg_trf_over_item",
        "clear_cb": "cfg_trf_over_clear",
        "step_cb": "cfg_edit_traffic_step",
        "over_cb": "cfg_edit_traffic_over",
        "step_state": TariffConfigState.entering_traffic_step,
        "over_state": TariffConfigState.entering_traffic_overrides,
        "step_title": "Шаг за трафик",
        "step_hint": "Цена в рублях за 1 ГБ сверх базового лимита.",
        "step_example": "5",
        "step_off": "<code>0</code> выключает автодоплату за трафик.",
        "data_key": "traffic_override_gb",
        "pick_first": "Сначала выберите вариант лимита трафика из списка.",
        "need_options": None,
        "ignore_not_modified": True,
        "options_cb": "cfg_edit_traffic",
        "options_state": TariffConfigState.entering_traffic,
        "options_title": "Варианты трафика",
        "options_ask": "Пришлите список ГБ через пробел или запятую.",
        "options_example": "100 200 500",
        "options_off": "Один только <code>0</code> — выбор трафика отключён, остаётся базовый лимит тарифа.",
    },
}


def effective_extra(tariff: Tariff, kind: str, value: int) -> tuple[int, bool]:
    """Доплата за вариант и признак, что она индивидуальная, а не по базовому шагу."""
    k = KIND[kind]
    override_extra = (getattr(tariff, k["overrides_field"], None) or {}).get(str(value))
    if override_extra is not None:
        return int(override_extra), True
    return k["formula"](tariff, value), False


def build_overrides_screen(tariff: Tariff, kind: str) -> tuple[str, InlineKeyboardMarkup]:
    k = KIND[kind]
    tariff_id = tariff.id
    base_price = int(tariff.price_rub or 0)
    options = k["screen_options"](getattr(tariff, k["options_field"]) or [])

    lines: list[str] = [
        menu_text(
            k["screen_title"],
            "Нажмите на вариант, чтобы задать доплату.",
            quote(f"Базовая цена тарифа: <b>{base_price}₽</b>"),
            quote(
                k["screen_zero_hint"],
                "Доплата <code>0</code> возвращает расчёт по базовому шагу.",
            ),
        ),
        "",
        "Текущие значения:",
    ]
    rows: list[list[InlineKeyboardButton]] = []

    for value in options:
        extra, custom = effective_extra(tariff, kind, value)
        name = k["short_zero"] if value == 0 else k["short"].format(value)
        lines.append(f"• {name}: доплата {extra}₽{' (индивидуальная доплата)' if custom else ''}")
        star = "★" if custom else ""
        label = f"{star} {name} — доплата +{extra}₽" if extra > 0 else f"{star} {name} — без доплаты"
        rows.append([InlineKeyboardButton(text=label.strip(), callback_data=f"{k['item_cb']}|{tariff_id}|{value}")])

    rows.append([
        InlineKeyboardButton(
            text="🧹 Сбросить свои доплаты",
            callback_data=f"{k['clear_cb']}|{tariff_id}",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ К конфигуратору",
            callback_data=f"edit_config|{tariff_id}",
        )
    ])

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def build_device_overrides_screen(tariff: Tariff) -> tuple[str, InlineKeyboardMarkup]:
    return build_overrides_screen(tariff, "dev")


def build_traffic_overrides_screen(tariff: Tariff) -> tuple[str, InlineKeyboardMarkup]:
    return build_overrides_screen(tariff, "trf")


def build_config_summary_text(tariff: Tariff) -> str:
    """Возвращает экран конфигуратора тарифа."""
    cfg = normalize_tariff_config(tariff.to_dict())

    base_devices = tariff.device_limit if tariff.device_limit is not None else "—"
    base_traffic = "безлимит" if tariff.traffic_limit is None else f"{tariff.traffic_limit} ГБ"

    device_options = cfg.get("device_options") or []
    traffic_options_gb = cfg.get("traffic_options_gb")

    if device_options:
        devices_choice = ", ".join("безлимит" if d == 0 else str(d) for d in device_options)
    else:
        devices_choice = f"выкл, {base_devices}"

    if traffic_options_gb is None:
        traffic_choice = "выкл"
    else:
        traffic_choice = ", ".join("безлимит" if g == 0 else f"{g} ГБ" for g in traffic_options_gb)

    device_step = getattr(tariff, "device_step_rub", None) or 0
    traffic_step = getattr(tariff, "traffic_step_rub", None) or 0
    device_overrides = getattr(tariff, "device_overrides", None) or {}
    traffic_overrides = getattr(tariff, "traffic_overrides", None) or {}

    device_rows = [f"Шаг: {device_step} ₽ за устройство"]
    for key, value in sorted(device_overrides.items(), key=lambda item: int(item[0])):
        label = "безлимит" if int(key) == 0 else f"{int(key)} шт"
        device_rows.append(f"{label}: {int(value)} ₽")

    traffic_rows = [f"Шаг: {traffic_step} ₽ за 1 ГБ"]
    for key, value in sorted(traffic_overrides.items(), key=lambda item: int(item[0])):
        label = "безлимит" if int(key) == 0 else f"{int(key)} ГБ"
        traffic_rows.append(f"{label}: {int(value)} ₽")

    body = card(
        section(
            "🎯 База",
            f"Срок: {tariff.duration_days} дн",
            f"Устройства: {base_devices}",
            f"Трафик: {base_traffic}",
            f"Цена: {tariff.price_rub or 0} ₽",
        ),
        section(
            "⚙️ Выбор клиента",
            "Срок: фиксированный",
            f"Устройства: {devices_choice}",
            f"Трафик: {traffic_choice}",
        ),
        section("📱 Доплата за устройства", *device_rows),
        section("📦 Доплата за трафик", *traffic_rows),
    )

    status = "включён" if getattr(tariff, "configurable", False) else "выключен"
    return menu_text("Конфигуратор", f"<b>{tariff.name}</b>", quote(f"Конфигуратор: {status}"), body)


@router.callback_query(F.data.startswith("edit_config|"), IsAdminFilter())
async def open_config_menu(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    tariff_id = int(callback.data.split("|")[1])

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()
    if not tariff:
        await callback.message.edit_text(menu_text("Конфигуратор", "❌ Тариф не найден."))
        return

    await state.set_state(TariffConfigState.choosing_section)
    await state.update_data(tariff_id=tariff_id)

    text = build_config_summary_text(tariff)
    await callback.message.edit_text(text=text, reply_markup=build_config_menu_kb(tariff_id))
