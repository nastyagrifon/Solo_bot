from datetime import datetime

from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import attributes

from database.models import Tariff
from filters.admin import IsAdminFilter

from ...panel.headers import menu_text, quote, section
from .. import router
from .common import (
    KIND,
    TariffConfigState,
    build_cancel_config_kb,
    build_config_menu_kb,
    build_config_summary_text,
    build_overrides_screen,
    effective_extra,
)


def register_pricing(kind: str):
    """Регистрирует шаг и доплаты для вида ("dev"/"trf"), возвращает шесть хендлеров по порядку."""
    k = KIND[kind]
    step_field = k["step_field"]
    overrides_field = k["overrides_field"]
    data_key = k["data_key"]

    @router.callback_query(F.data.startswith(f"{k['step_cb']}|"), TariffConfigState.choosing_section, IsAdminFilter())
    async def ask_step(callback: CallbackQuery, state: FSMContext):
        tariff_id = int(callback.data.split("|")[1])
        await state.set_state(k["step_state"])
        await state.update_data(tariff_id=tariff_id)

        text = menu_text(
            k["step_title"],
            k["step_hint"],
            quote(f"Например: <code>{k['step_example']}</code>"),
            quote(k["step_off"]),
        )
        await callback.message.edit_text(text=text, reply_markup=build_cancel_config_kb(tariff_id))

    @router.message(k["step_state"], IsAdminFilter())
    async def save_step(message: Message, state: FSMContext, session: AsyncSession):
        data = await state.get_data()
        tariff_id = data["tariff_id"]

        try:
            price = int(message.text.strip())
            if price < 0:
                raise ValueError
        except ValueError:
            await message.answer(
                menu_text("Конфигуратор", "❌ Нужно число от нуля."),
                reply_markup=build_cancel_config_kb(tariff_id),
            )
            return

        result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
        tariff = result.scalar_one_or_none()
        if not tariff:
            await message.answer(menu_text("Конфигуратор", "❌ Тариф не найден."))
            await state.clear()
            return

        setattr(tariff, step_field, price)
        tariff.updated_at = datetime.utcnow()

        await state.set_state(TariffConfigState.choosing_section)

        text = build_config_summary_text(tariff)
        await message.answer(text=text, reply_markup=build_config_menu_kb(tariff_id))

    @router.callback_query(F.data.startswith(f"{k['over_cb']}|"), TariffConfigState.choosing_section, IsAdminFilter())
    async def open_overrides_menu(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
        tariff_id = int(callback.data.split("|")[1])

        result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
        tariff = result.scalar_one_or_none()
        if not tariff:
            await callback.message.edit_text(menu_text("Конфигуратор", "❌ Тариф не найден."))
            return

        if k["need_options"] and not (getattr(tariff, k["options_field"]) or []):
            title, text, hint = k["need_options"]
            await callback.message.edit_text(
                menu_text(title, text, quote(hint)),
                reply_markup=build_config_menu_kb(tariff_id),
            )
            return

        await state.set_state(k["over_state"])
        await state.update_data(tariff_id=tariff_id, **{data_key: None})

        text, markup = build_overrides_screen(tariff, kind)
        await callback.message.edit_text(text=menu_text("Конфигуратор", text), reply_markup=markup)

    @router.callback_query(F.data.startswith(f"{k['item_cb']}|"), k["over_state"], IsAdminFilter())
    async def choose_override_option(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
        parts = callback.data.split("|")
        tariff_id = int(parts[1])
        value = int(parts[2])

        await state.update_data(tariff_id=tariff_id, **{data_key: value})

        result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
        tariff = result.scalar_one_or_none()
        if not tariff:
            await callback.message.edit_text(menu_text("Конфигуратор", "❌ Тариф не найден."))
            await state.clear()
            return

        extra, custom = effective_extra(tariff, kind, value)
        note = "индивидуальная доплата" if custom else "доплата по базовому шагу"
        label = k["long_zero"] if value == 0 else k["long"].format(value)

        text = menu_text(
            "Доплата за вариант",
            f"<b>{label}</b>",
            section("💰 Сейчас", f"Доплата: {extra} ₽", f"Расчёт: {note}"),
            quote("Пришлите новую доплату в рублях. Ноль вернёт расчёт по базовому шагу."),
        )
        await callback.message.edit_text(text=text, reply_markup=build_cancel_config_kb(tariff_id))

    @router.callback_query(F.data.startswith(f"{k['clear_cb']}|"), k["over_state"], IsAdminFilter())
    async def clear_overrides(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
        tariff_id = int(callback.data.split("|")[1])

        result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
        tariff = result.scalar_one_or_none()
        if not tariff:
            await callback.message.edit_text(menu_text("Конфигуратор", "❌ Тариф не найден."))
            await state.clear()
            return

        setattr(tariff, overrides_field, None)
        tariff.updated_at = datetime.utcnow()

        text, markup = build_overrides_screen(tariff, kind)
        try:
            await callback.message.edit_text(text=menu_text("Конфигуратор", text), reply_markup=markup)
        except TelegramBadRequest as e:
            # Исторически глушится только у трафика; у устройств ошибка уходит дальше.
            if not k["ignore_not_modified"] or "message is not modified" not in str(e):
                raise

    @router.message(k["over_state"], IsAdminFilter())
    async def save_override_price(message: Message, state: FSMContext, session: AsyncSession):
        data = await state.get_data()
        tariff_id = data.get("tariff_id")
        value = data.get(data_key)

        if not tariff_id or value is None:
            await message.answer(menu_text("Конфигуратор", k["pick_first"]))
            return

        try:
            extra_price = int(message.text.strip())
            if extra_price < 0:
                raise ValueError
        except ValueError:
            await message.answer(
                menu_text("Конфигуратор", "❌ Нужно число от нуля."),
                reply_markup=build_cancel_config_kb(int(tariff_id)),
            )
            return

        result = await session.execute(select(Tariff).where(Tariff.id == int(tariff_id)))
        tariff = result.scalar_one_or_none()
        if not tariff:
            await message.answer(menu_text("Конфигуратор", "❌ Тариф не найден."))
            await state.clear()
            return

        existing_overrides = getattr(tariff, overrides_field)
        overrides = dict(existing_overrides) if existing_overrides else {}
        key = str(int(value))

        if extra_price == 0:
            overrides.pop(key, None)
        else:
            overrides[key] = extra_price

        setattr(tariff, overrides_field, overrides if overrides else None)
        attributes.flag_modified(tariff, overrides_field)
        tariff.updated_at = datetime.utcnow()

        await state.update_data(**{data_key: None})

        text, markup = build_overrides_screen(tariff, kind)
        await message.answer(text=menu_text("Конфигуратор", text), reply_markup=markup)

    return ask_step, save_step, open_overrides_menu, choose_override_option, clear_overrides, save_override_price
