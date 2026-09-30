from datetime import datetime, timezone
from typing import Any

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import MODES_CONFIG
from core.settings.tariffs_config import normalize_tariff_config
from database import get_key_details, get_tariff_by_id
from handlers.utils import edit_or_send_message, render_text
from hooks.hook_buttons import insert_hook_buttons
from hooks.processors import process_addons_menu
from logger import logger
from services.formatting import get_plural_form
from services.tariffs.tariff_display import GB
from settings.buttons import BACK
from settings.texts import (
    ADDONS_HINT_BOTH_OPTIONS_TEXT,
    ADDONS_HINT_SINGLE_OPTION_TEXT,
    ADDONS_PACK_HINT_BOTH,
    ADDONS_PACK_HINT_DEVICES,
    ADDONS_PACK_HINT_TRAFFIC,
    ADDONS_TEXT,
    UNLIMITED_DEVICES_LABEL,
    UNLIMITED_ROW_VALUE,
    UNLIMITED_TRAFFIC_LABEL,
)

from ...keys.utils import build_key_callback, resolve_key


class KeyAddonConfigState(StatesGroup):
    configuring = State()


def format_devices_label(value, default_text: str = "по умолчанию") -> str:
    if value is None:
        return default_text
    value_int = int(value)
    if value_int <= 0:
        return UNLIMITED_DEVICES_LABEL
    return f"{value_int} {get_plural_form(value_int, 'устройство', 'устройства', 'устройств')}"


def format_traffic_label(value, default_text: str = "по умолчанию") -> str:
    if value is None:
        return default_text
    value_int = int(value)
    if value_int <= 0:
        return UNLIMITED_TRAFFIC_LABEL
    return f"{value_int} ГБ"


def device_option_label(value: int) -> str:
    """Подпись кнопки выбора устройств: ноль означает безлимит."""
    if int(value) == 0:
        return UNLIMITED_DEVICES_LABEL.capitalize()
    return f"{value} {get_plural_form(value, 'устройство', 'устройства', 'устройств')}"


def traffic_option_label(value: int) -> str:
    """Подпись кнопки выбора трафика: ноль означает безлимит."""
    if int(value) == 0:
        return UNLIMITED_TRAFFIC_LABEL.capitalize()
    return f"{value} ГБ"


def is_not_downgrade(current_value, new_value) -> bool:
    if current_value is None:
        return True
    current_int = int(current_value)
    new_int = int(new_value)
    current_cmp = current_int if current_int > 0 else 10**9
    new_cmp = new_int if new_int > 0 else 10**9
    return new_cmp >= current_cmp


def calc_remaining_ratio_seconds(expiry_time: Any, tariff: dict) -> tuple[int, int]:
    """Секунды до конца подписки и длительность периода."""
    duration_days = int(tariff.get("duration_days") or 0) or 30
    total_seconds = max(1, duration_days * 86400)

    if not expiry_time:
        return total_seconds, total_seconds

    expiry_dt: datetime | None = None

    if isinstance(expiry_time, datetime):
        expiry_dt = expiry_time
    elif isinstance(expiry_time, int | float):
        ts = float(expiry_time)
        if ts > 10_000_000_000:
            ts = ts / 1000.0
        try:
            expiry_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            expiry_dt = None
    elif isinstance(expiry_time, str):
        try:
            expiry_dt = datetime.fromisoformat(expiry_time.replace("Z", "+00:00"))
        except Exception:
            expiry_dt = None

    if expiry_dt is None:
        return total_seconds, total_seconds

    now_utc = datetime.now(timezone.utc)
    if expiry_dt.tzinfo is None:
        expiry_utc = expiry_dt.replace(tzinfo=timezone.utc)
    else:
        expiry_utc = expiry_dt.astimezone(timezone.utc)

    remaining_seconds = int((expiry_utc - now_utc).total_seconds())
    if remaining_seconds <= 0:
        return 0, total_seconds

    if remaining_seconds > total_seconds:
        remaining_seconds = total_seconds

    return remaining_seconds, total_seconds


def limit_row_value(label: str) -> str:
    """Возвращает значение лимита для строки таблицы, без повтора её метки."""
    if label in (UNLIMITED_DEVICES_LABEL, UNLIMITED_TRAFFIC_LABEL):
        return UNLIMITED_ROW_VALUE
    for word in ("устройство", "устройства", "устройств"):
        if label.endswith(f" {word}"):
            return label[: -len(word) - 1]
    return label


def build_addons_screen_text(
    *,
    tariff_name: str,
    current_devices_label: str,
    current_traffic_label: str,
    new_devices_label: str,
    new_traffic_label: str,
    has_device_choice: bool,
    has_traffic_choice: bool,
    total_price_text: str,
    extra_price_text: str,
    downgrade_warning: str | None = None,
) -> str:
    """Собирает экран докупки опций по шаблону из файла текстов."""
    hint = ADDONS_HINT_BOTH_OPTIONS_TEXT if has_device_choice and has_traffic_choice else ADDONS_HINT_SINGLE_OPTION_TEXT
    if downgrade_warning:
        hint = f"{downgrade_warning}\n{hint}"

    return render_text(
        ADDONS_TEXT,
        tariff_name=tariff_name,
        devices_now=limit_row_value(current_devices_label) if has_device_choice else "",
        devices_pack="",
        devices_new=limit_row_value(new_devices_label) if has_device_choice else "",
        traffic_now=limit_row_value(current_traffic_label) if has_traffic_choice else "",
        traffic_pack="",
        traffic_new=limit_row_value(new_traffic_label) if has_traffic_choice else "",
        price_total=total_price_text,
        price_extra=extra_price_text,
        hint=hint,
    )


def build_addons_pack_screen_text(
    *,
    tariff_name: str,
    current_devices_label: str,
    current_traffic_label: str | None,
    selected_devices_label: str | None,
    selected_traffic_label: str | None,
    total_devices_label: str | None,
    total_traffic_label: str | None,
    extra_price_text: str,
    has_device_option: bool,
    has_traffic_option: bool,
) -> str:
    """Собирает экран докупки пакета по шаблону из файла текстов."""
    if has_device_option and has_traffic_option:
        hint = ADDONS_PACK_HINT_BOTH
    elif has_traffic_option:
        hint = ADDONS_PACK_HINT_TRAFFIC
    elif has_device_option:
        hint = ADDONS_PACK_HINT_DEVICES
    else:
        hint = ""

    def pack_value(label: str | None, enabled: bool) -> str:
        return f"+{limit_row_value(label)}" if enabled and label is not None else ""

    return render_text(
        ADDONS_TEXT,
        tariff_name=tariff_name,
        devices_now=limit_row_value(current_devices_label),
        devices_pack=pack_value(selected_devices_label, has_device_option),
        devices_new=limit_row_value(total_devices_label) if has_device_option and total_devices_label else "",
        traffic_now=limit_row_value(current_traffic_label) if current_traffic_label else "",
        traffic_pack=pack_value(selected_traffic_label, has_traffic_option),
        traffic_new=limit_row_value(total_traffic_label) if has_traffic_option and total_traffic_label else "",
        price_total="",
        price_extra=extra_price_text,
        hint=hint,
    )


# Общее для config_mode и pack_mode: режимы различаются флагом pack.


def _log_tag(pack: bool, text: str) -> str:
    """Префикс лога режима: «[ADDONS] Текст» или «[ADDONS] PACK_MODE: текст»."""
    if pack:
        return f"[ADDONS] PACK_MODE: {text}"
    return f"[ADDONS] {text[:1].upper()}{text[1:]}"


def sort_options(raw_options: list) -> list:
    """Варианты по возрастанию, безлимит (0) в конце; мусор оставляем как есть."""
    try:
        return sorted(raw_options, key=lambda v: (int(v) == 0, int(v)))
    except (TypeError, ValueError):
        return raw_options


def int_options(options) -> list[int]:
    """Только целые варианты, нечисловые пропускаем."""
    result: list[int] = []
    for value in options:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return result


async def load_key_for_addons(callback: CallbackQuery, session: AsyncSession, *, pack: bool):
    """Начало start_key_addons: подписка, владелец, тариф и сырые варианты.

    None - пользователю уже ответили, дальше не идём.
    """
    key_ref = callback.data.split("|", 1)[1]
    key_obj = await resolve_key(session, callback.from_user.id, key_ref)
    email = key_obj.email if key_obj else key_ref
    mode = "PACK_MODE " if pack else ""
    logger.debug(f"[ADDONS] {mode}start_key_addons: tg_id={callback.from_user.id} email={email}")

    record = await get_key_details(session, email)
    if not record:
        logger.warning(_log_tag(pack, f"подписка {email} не найдена"))
        await callback.message.answer("❌ Подписка не найдена.")
        return None
    if record.get("tg_id") != callback.from_user.id:
        await callback.answer("Доступ запрещён.", show_alert=True)
        return None

    tariff_id = record.get("tariff_id")
    if not tariff_id:
        logger.warning(_log_tag(pack, f"для подписки {email} не назначен тариф"))
        await callback.message.answer("❌ Для этой подписки тариф не назначен, расширение недоступно.")
        return None

    tariff = await get_tariff_by_id(session, int(tariff_id))
    if not tariff:
        logger.error(_log_tag(pack, f"тариф {tariff_id} не найден для email={email}"))
        await callback.message.answer("❌ Тариф не найден.")
        return None

    if not tariff.get("configurable"):
        logger.info(_log_tag(pack, f"тариф {tariff_id} не конфигурируемый, расширение недоступно"))
        await callback.message.answer("❌ Для этого тарифа расширение через конфигуратор недоступно.")
        return None

    cfg = normalize_tariff_config(tariff)
    raw_device_options = cfg.get("device_options") or tariff.get("device_options") or []
    raw_traffic_options = cfg.get("traffic_options_gb") or tariff.get("traffic_options_gb") or []
    return email, record, tariff_id, tariff, cfg, raw_device_options, raw_traffic_options


def log_start_options(pack: bool, email, tariff_id, device_options, traffic_options) -> None:
    mode = "PACK_MODE " if pack else ""
    logger.info(
        f"[ADDONS] {mode}start_key_addons options: "
        f"email={email} tariff_id={tariff_id} "
        f"device_options={device_options} traffic_options={traffic_options}"
    )


def current_limits_from_record(record: dict, tariff: dict) -> tuple[int | None, int | None]:
    """Текущие лимиты подписки: current_* из БД, иначе selected_*, иначе база тарифа."""
    selected_device_limit_db = record.get("selected_device_limit")
    selected_traffic_limit_db = record.get("selected_traffic_limit")
    current_device_limit_db = record.get("current_device_limit")
    current_traffic_limit_db = record.get("current_traffic_limit")

    base_devices = tariff.get("device_limit")
    base_devices = int(base_devices) if base_devices is not None else None

    base_traffic_bytes = tariff.get("traffic_limit")
    base_traffic_gb = int(base_traffic_bytes / GB) if base_traffic_bytes else None

    current_devices = (
        int(current_device_limit_db)
        if current_device_limit_db is not None
        else (int(selected_device_limit_db) if selected_device_limit_db is not None else base_devices)
    )
    current_traffic_gb = (
        int(current_traffic_limit_db)
        if current_traffic_limit_db is not None
        else (int(selected_traffic_limit_db) if selected_traffic_limit_db is not None else base_traffic_gb)
    )
    return current_devices, current_traffic_gb


async def load_addons_screen_state(callback: CallbackQuery, state: FSMContext, session: AsyncSession, *, pack: bool):
    """Начало render_addons_screen: состояние, тариф и целые варианты.

    В pack-режиме пустые варианты из состояния добираются из тарифа.
    None - пользователю уже ответили, состояние сброшено.
    """
    data = await state.get_data()
    email = data.get("addon_key_email")
    tariff_id = data.get("addon_tariff_id")
    cfg = data.get("addon_tariff_config") or {}

    mode = " PACK_MODE" if pack else ""
    logger.debug(
        f"[ADDONS] render_addons_screen{mode} start: tg_id={callback.from_user.id} "
        f"email={email} tariff_id={tariff_id} data={data}"
    )

    if not email or not tariff_id:
        logger.warning(_log_tag(pack, f"нет email или tariff_id в состоянии: {data}"))
        await callback.message.answer("❌ Данные для изменения подписки не найдены.")
        await state.clear()
        return None

    tariff = await get_tariff_by_id(session, int(tariff_id))
    if not tariff:
        logger.error(_log_tag(pack, f"тариф {tariff_id} не найден в render_addons_screen"))
        await callback.message.answer("❌ Тариф не найден.")
        await state.clear()
        return None

    raw_device_options = cfg.get("device_options") or []
    raw_traffic_options = cfg.get("traffic_options_gb") or []
    if pack:
        raw_device_options = raw_device_options or tariff.get("device_options") or []
        raw_traffic_options = raw_traffic_options or tariff.get("traffic_options_gb") or []

    device_int_options = int_options(sort_options(raw_device_options))
    traffic_int_options = int_options(sort_options(raw_traffic_options))
    return data, email, tariff, device_int_options, traffic_int_options


def add_option_rows(
    builder: InlineKeyboardBuilder,
    email: str,
    device_options: list[int],
    selected_devices,
    traffic_options: list[int],
    selected_traffic_gb,
) -> None:
    """Кнопки выбора устройств и трафика: степпер ◀️ ▶️ или сетка с ✅."""

    def _addon_stepper_row(options, selected, cb_prefix, label_fn):
        options = sort_options(options)
        cur = int(selected) if selected is not None else options[0]
        try:
            idx = options.index(cur)
        except ValueError:
            idx = 0
        prev_val = options[idx - 1] if idx > 0 else options[idx]
        next_val = options[idx + 1] if idx < len(options) - 1 else options[idx]
        left = "◀️" if idx > 0 else "▫️"
        right = "▶️" if idx < len(options) - 1 else "▫️"
        return [
            InlineKeyboardButton(text=left, callback_data=f"{cb_prefix}|{email}|{prev_val}"),
            InlineKeyboardButton(text=label_fn(options[idx]), callback_data=f"{cb_prefix}|{email}|{options[idx]}"),
            InlineKeyboardButton(text=right, callback_data=f"{cb_prefix}|{email}|{next_val}"),
        ]

    use_pagination = bool((MODES_CONFIG or {}).get("TARIFF_OPTIONS_PAGINATION", True))
    if use_pagination:
        if device_options:
            builder.row(
                *_addon_stepper_row(device_options, selected_devices, "key_addons_devices", device_option_label)
            )
        if traffic_options:
            builder.row(
                *_addon_stepper_row(traffic_options, selected_traffic_gb, "key_addons_traffic", traffic_option_label)
            )
        return

    device_buttons = [
        InlineKeyboardButton(
            text=device_option_label(v)
            + (" ✅" if selected_devices is not None and int(v) == int(selected_devices) else ""),
            callback_data=f"key_addons_devices|{email}|{v}",
        )
        for v in device_options
    ]
    traffic_buttons = [
        InlineKeyboardButton(
            text=traffic_option_label(v)
            + (" ✅" if selected_traffic_gb is not None and int(v) == int(selected_traffic_gb) else ""),
            callback_data=f"key_addons_traffic|{email}|{v}",
        )
        for v in traffic_options
    ]
    if device_buttons and traffic_buttons:
        max_len = max(len(device_buttons), len(traffic_buttons))
        for i in range(max_len):
            row = []
            if i < len(device_buttons):
                row.append(device_buttons[i])
            if i < len(traffic_buttons):
                row.append(traffic_buttons[i])
            builder.row(*row)
    elif device_buttons:
        for i in range(0, len(device_buttons), 2):
            builder.row(*device_buttons[i : i + 2])
    elif traffic_buttons:
        for i in range(0, len(traffic_buttons), 2):
            builder.row(*traffic_buttons[i : i + 2])


async def send_addons_screen(
    callback: CallbackQuery,
    session: AsyncSession,
    builder: InlineKeyboardBuilder,
    text: str,
    data: dict,
    email: str,
) -> None:
    """Хвост экрана: «Назад», кнопки модулей и отправка."""
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=build_key_callback("view_key", data.get("addon_key_client_id"), email),
        )
    )

    module_buttons = await process_addons_menu(email=email, session=session)
    builder = insert_hook_buttons(builder, module_buttons)

    await edit_or_send_message(
        target_message=callback.message,
        text=text,
        reply_markup=builder.as_markup(),
    )
    await callback.answer()
