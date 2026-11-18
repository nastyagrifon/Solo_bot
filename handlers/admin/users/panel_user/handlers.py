"""Экраны пользователя панели из карточки ключа: тонкие обработчики.

Запросы к панели — в service, тексты и кнопки — в views. FSM общий с другими
экранами админки, поэтому храним только свои ключи с префиксом и убираем
только их.
"""

import functools

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from filters.admin import IsAdminFilter
from services.users_utils import build_admin_key_ref, resolve_admin_key

from ..keyboard import AdminUserEditorCallback
from . import service, validate, views
from .service import PanelUserError
from .states import PanelUserState
from .views import PanelUserCallback


router = Router()

PREFIX = "kpu_"
# Без флага общий middleware отвечает на нажатие до обработчика, и show_alert со сбоем уже не дойдёт.
POPUP = {"popup": True}

# Поле ввода → разборщик; имя поля в панели — в views.FIELD_PROMPTS.
FIELDS = {"tg": validate.parse_telegram_id, "tag": validate.parse_tag, "note": validate.parse_description}


def _panel_field(field: str) -> str:
    return views.FIELD_PROMPTS[field][2]


def loud(handler):
    """Сбой панели показываем админу: немой отказ выглядит сломанной кнопкой."""

    @functools.wraps(handler)
    async def wrapper(event, *args, **kwargs):
        try:
            return await handler(event, *args, **kwargs)
        except PanelUserError as error:
            if isinstance(event, CallbackQuery):
                await event.answer(f"❌ {error}", show_alert=True)
            else:
                await event.answer(f"❌ {error}")

    return wrapper


async def _forget(state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_data({k: v for k, v in data.items() if not k.startswith(PREFIX)})
    if await state.get_state() == PanelUserState.waiting_value.state:
        await state.set_state(None)


async def _key(session: AsyncSession, base: PanelUserCallback):
    key = await resolve_admin_key(session, base.user_id, base.key_ref)
    if not key:
        raise PanelUserError("Подписка не найдена.")
    return key


async def _edit(callback: CallbackQuery, text: str, kb) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


async def _card(session: AsyncSession, key, base: PanelUserCallback):
    user, external = await service.load(session, key, "external")
    return views.card_text(user, key.tg_id, external), views.card_kb(base)


async def _show_saved(event, session: AsyncSession, state: FSMContext, key, base: PanelUserCallback) -> None:
    """Перечитывает карточку после записи; сбой перечитывания не выдаём за сбой записи."""
    await _forget(state)
    try:
        text, kb = await _card(session, key, base)
    except PanelUserError as error:
        raise PanelUserError(f"Сохранено, но карточку перечитать не удалось: {error}") from error
    if isinstance(event, CallbackQuery):
        await _edit(event, text, kb)
        await event.answer("✅ Сохранено")
    else:
        await event.answer(text, reply_markup=kb)


async def _picked(state: FSMContext, name: str, idx: int) -> str:
    pairs = (await state.get_data()).get(PREFIX + name) or []
    if not 0 <= idx < len(pairs):
        raise PanelUserError("Список устарел, откройте его заново.")
    return pairs[idx][0]


def _pairs(squads: list[dict]) -> list[list[str]]:
    return [[str(s["uuid"]), str(s.get("name") or s["uuid"])] for s in squads if s.get("uuid")]


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_panel_user"), IsAdminFilter(), flags=POPUP)
@loud
async def handle_panel_user_entry(
    callback: CallbackQuery, callback_data: AdminUserEditorCallback, session: AsyncSession, state: FSMContext
):
    key = await resolve_admin_key(session, callback_data.user_id, callback_data.data)
    if not key:
        raise PanelUserError("Подписка не найдена.")
    # Короткая ссылка вместо исходной: старые кнопки могли нести почту, а она не влезет в 64 байта.
    base = PanelUserCallback(
        action="card", user_id=callback_data.user_id, key_ref=build_admin_key_ref(key.client_id, key.email)
    )
    await _forget(state)
    await _edit(callback, *await _card(session, key, base))


@router.callback_query(PanelUserCallback.filter(F.action == "card"), IsAdminFilter(), flags=POPUP)
@loud
async def handle_panel_user_card(
    callback: CallbackQuery, callback_data: PanelUserCallback, session: AsyncSession, state: FSMContext
):
    key = await _key(session, callback_data)
    await _forget(state)
    await _edit(callback, *await _card(session, key, callback_data))


@router.callback_query(PanelUserCallback.filter(F.action == "ext"), IsAdminFilter(), flags=POPUP)
@loud
async def handle_external_menu(
    callback: CallbackQuery, callback_data: PanelUserCallback, session: AsyncSession, state: FSMContext
):
    user, squads = await service.load(session, await _key(session, callback_data), "external")
    pairs = _pairs(squads)
    await state.update_data({PREFIX + "ext": pairs})
    await _edit(callback, views.external_text(), views.external_kb(callback_data, pairs, user.get("externalSquadUuid")))


@router.callback_query(PanelUserCallback.filter(F.action == "ext_set"), IsAdminFilter(), flags=POPUP)
@loud
async def handle_external_set(
    callback: CallbackQuery, callback_data: PanelUserCallback, session: AsyncSession, state: FSMContext
):
    squad = None if callback_data.idx == -1 else await _picked(state, "ext", callback_data.idx)
    key = await _key(session, callback_data)
    await service.set_field(session, key, "externalSquadUuid", squad)
    await _show_saved(callback, session, state, key, callback_data)


@router.callback_query(PanelUserCallback.filter(F.action == "int"), IsAdminFilter(), flags=POPUP)
@loud
async def handle_internal_menu(
    callback: CallbackQuery, callback_data: PanelUserCallback, session: AsyncSession, state: FSMContext
):
    user, squads = await service.load(session, await _key(session, callback_data), "internal")
    pairs, selected = _pairs(squads), service.internal_squad_uuids(user)
    await state.update_data({PREFIX + "int": pairs, PREFIX + "int_sel": selected})
    await _edit(callback, views.internal_text(), views.internal_kb(callback_data, pairs, selected))


@router.callback_query(PanelUserCallback.filter(F.action == "int_tg"), IsAdminFilter(), flags=POPUP)
@loud
async def handle_internal_toggle(callback: CallbackQuery, callback_data: PanelUserCallback, state: FSMContext):
    uuid = await _picked(state, "int", callback_data.idx)
    data = await state.get_data()
    selected = service.toggle(data.get(PREFIX + "int_sel") or [], uuid)
    await state.update_data({PREFIX + "int_sel": selected})
    await _edit(callback, views.internal_text(), views.internal_kb(callback_data, data[PREFIX + "int"], selected))


@router.callback_query(PanelUserCallback.filter(F.action == "int_save"), IsAdminFilter(), flags=POPUP)
@loud
async def handle_internal_save(
    callback: CallbackQuery, callback_data: PanelUserCallback, session: AsyncSession, state: FSMContext
):
    data = await state.get_data()
    if PREFIX + "int_sel" not in data:
        raise PanelUserError("Список устарел, откройте его заново.")
    key = await _key(session, callback_data)
    await service.set_field(session, key, service.INTERNAL, data[PREFIX + "int_sel"])
    await _show_saved(callback, session, state, key, callback_data)


@router.callback_query(
    PanelUserCallback.filter((F.action == "ask") & F.field.in_(FIELDS)), IsAdminFilter(), flags=POPUP
)
@loud
async def handle_field_ask(
    callback: CallbackQuery, callback_data: PanelUserCallback, session: AsyncSession, state: FSMContext
):
    key = await _key(session, callback_data)
    user, _ = await service.load(session, key)
    await state.set_state(PanelUserState.waiting_value)
    card_cb = PanelUserCallback(action="card", user_id=callback_data.user_id, key_ref=callback_data.key_ref)
    await state.update_data({PREFIX + "field": callback_data.field, PREFIX + "card": card_cb.pack()})
    await _edit(
        callback,
        views.ask_text(callback_data.field, user, key.tg_id),
        views.ask_kb(callback_data, callback_data.field, key.tg_id),
    )


@router.callback_query(
    PanelUserCallback.filter((F.action == "clr") & F.field.in_(FIELDS)), IsAdminFilter(), flags=POPUP
)
@loud
async def handle_field_clear(
    callback: CallbackQuery, callback_data: PanelUserCallback, session: AsyncSession, state: FSMContext
):
    key = await _key(session, callback_data)
    await service.set_field(session, key, _panel_field(callback_data.field), None)
    await _show_saved(callback, session, state, key, callback_data)


@router.callback_query(PanelUserCallback.filter(F.action == "own"), IsAdminFilter(), flags=POPUP)
@loud
async def handle_telegram_id_from_owner(
    callback: CallbackQuery, callback_data: PanelUserCallback, session: AsyncSession, state: FSMContext
):
    key = await _key(session, callback_data)
    if not key.tg_id:
        raise PanelUserError("У владельца ключа нет TG ID.")
    await service.set_field(session, key, "telegramId", int(key.tg_id))
    await _show_saved(callback, session, state, key, callback_data)


@router.message(StateFilter(PanelUserState.waiting_value), IsAdminFilter())
@loud
async def handle_field_input(message: Message, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    field, packed = data.get(PREFIX + "field"), data.get(PREFIX + "card")
    if field not in FIELDS or not packed:
        await _forget(state)
        raise PanelUserError("Ввод устарел, откройте поле заново.")
    try:
        value = FIELDS[field](message.text or "")
    except ValueError as error:
        await message.answer(str(error))
        return
    base = PanelUserCallback.unpack(packed)
    key = await _key(session, base)
    await service.set_field(session, key, _panel_field(field), value)
    await _show_saved(message, session, state, key, base)
