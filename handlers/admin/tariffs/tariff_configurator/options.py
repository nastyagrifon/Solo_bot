import re

from datetime import datetime

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Tariff
from filters.admin import IsAdminFilter

from ...panel.headers import menu_text, quote
from .. import router
from .common import (
    KIND,
    TariffConfigState,
    build_cancel_config_kb,
    build_config_menu_kb,
    build_config_summary_text,
)


def _register_options(kind: str):
    """Регистрирует ввод списка вариантов для вида ("dev"/"trf"), возвращает оба хендлера."""
    k = KIND[kind]
    field = k["options_field"]
    example = quote(f"Например: <code>{k['options_example']}</code>")

    @router.callback_query(F.data.startswith(f"{k['options_cb']}|"), TariffConfigState.choosing_section, IsAdminFilter())
    async def ask_config(callback: CallbackQuery, state: FSMContext):
        tariff_id = int(callback.data.split("|")[1])
        await state.set_state(k["options_state"])
        await state.update_data(tariff_id=tariff_id)

        text = menu_text(
            k["options_title"],
            k["options_ask"],
            example,
            quote(
                "<code>0</code> в списке — вариант «безлимит».",
                k["options_off"],
            ),
        )
        await callback.message.edit_text(text=text, reply_markup=build_cancel_config_kb(tariff_id))

    @router.message(k["options_state"], IsAdminFilter())
    async def save_config(message: Message, state: FSMContext, session: AsyncSession):
        data = await state.get_data()
        tariff_id = data["tariff_id"]
        raw_text = message.text.strip()

        result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
        tariff = result.scalar_one_or_none()
        if not tariff:
            await message.answer(menu_text("Конфигуратор", "❌ Тариф не найден."))
            await state.clear()
            return

        if raw_text == "0":
            setattr(tariff, field, None)
        else:
            try:
                parts = [p for p in re.split(r"[,\s]+", raw_text) if p.strip()]
                if not parts:
                    raise ValueError
                values: list[int] = []
                for part in parts:
                    v = int(part)
                    if v < 0:
                        raise ValueError
                    values.append(v)
                values = sorted(set(values))
                setattr(tariff, field, values)
            except Exception:
                await message.answer(
                    menu_text(
                        "Некорректные значения",
                        "Нужны числа 0 и больше через пробел или запятую.",
                        example,
                    ),
                    reply_markup=build_cancel_config_kb(tariff_id),
                )
                return

        tariff.updated_at = datetime.utcnow()

        await state.set_state(TariffConfigState.choosing_section)

        text = build_config_summary_text(tariff)
        await message.answer(text=text, reply_markup=build_config_menu_kb(tariff_id))

    return ask_config, save_config


ask_devices_config, save_devices_config = _register_options("dev")
ask_traffic_config, save_traffic_config = _register_options("trf")
