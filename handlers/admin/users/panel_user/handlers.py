"""Правка пользователя панели из карточки ключа: тонкие обработчики.

Поля показываются секцией в самой карточке ключа (key_card_section), кнопки
правки — в её клавиатуре. Запросы к панели — в service, тексты — в views.
FSM общий с другими экранами админки, поэтому храним только свои ключи с
префиксом и убираем только их.
"""

import functools

from types import SimpleNamespace

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from database.access.resolution import UserId, get_user_by_tg_id
from filters.admin import IsAdminFilter
from logger import logger
from services.operations.key_transfer import KeyTransferError, transfer_key
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
FIELDS = {"tag": validate.parse_tag, "note": validate.parse_description}
TRANSFER = "tg"
SAVED = "✅ Сохранено"
# Кнопки карточки ключа → поле ввода.
ASK_ACTIONS = {"users_kpu_tag": "tag", "users_kpu_note": "note", "users_kpu_tg": TRANSFER}


def loud(handler):
    """Сбой показываем админу: немой отказ выглядит сломанной кнопкой."""

    @functools.wraps(handler)
    async def wrapper(event, *args, **kwargs):
        try:
            return await handler(event, *args, **kwargs)
        except (PanelUserError, KeyTransferError) as error:
            if isinstance(event, CallbackQuery):
                await event.answer(f"❌ {error}", show_alert=True)
            else:
                await event.answer(f"❌ {error}")

    return wrapper


async def key_card_section(session: AsyncSession, key) -> str:
    """Секция «Панель» для карточки ключа; сбой панели карточку не ломает."""
    try:
        user, external = await service.load(session, key, "external")
    except PanelUserError:
        return views.panel_section_failed()
    except Exception as error:
        logger.warning(f"[PanelUser] секция панели для {key.client_id}: {error}")
        return views.panel_section_failed()
    return views.panel_section(user, external)


async def _forget(state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_data({k: v for k, v in data.items() if not k.startswith(PREFIX)})
    if await state.get_state() == PanelUserState.waiting_value.state:
        await state.set_state(None)


async def _key(session: AsyncSession, user_id: int, key_ref: str):
    key = await resolve_admin_key(session, user_id, key_ref)
    if not key:
        raise PanelUserError("Подписка не найдена.")
    return key


async def _base(session: AsyncSession, callback_data: AdminUserEditorCallback):
    key = await _key(session, callback_data.user_id, callback_data.data)
    # Короткая ссылка вместо исходной: старые кнопки могли нести почту, а она не влезет в 64 байта.
    ref = build_admin_key_ref(key.client_id, key.email)
    return key, PanelUserCallback(action="", user_id=callback_data.user_id, key_ref=ref)


async def _edit(callback: CallbackQuery, text: str, kb) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


async def _show_key_card(
    event, session: AsyncSession, state: FSMContext, user_id: int, key_ref: str, notice: str | None = None
) -> None:
    # Импорт внутри: карточка ключа сама импортирует этот пакет ради секции «Панель».
    from ..users_keys.edit import handle_key_edit

    await _forget(state)
    target = AdminUserEditorCallback(action="users_key_edit", data=key_ref, user_id=user_id)
    if isinstance(event, CallbackQuery):
        if notice:
            await event.answer(notice)
        await handle_key_edit(event, target, session)
    else:
        # Карточка умеет только править сообщение; после ввода текста шлём её новым.
        await handle_key_edit(SimpleNamespace(message=SimpleNamespace(edit_text=event.answer)), target, session)


def _owner_label(key) -> str:
    return str(key.tg_id) if key.tg_id else f"id {key.user_id}"


async def _picked(state: FSMContext, name: str, idx: int) -> str:
    pairs = (await state.get_data()).get(PREFIX + name) or []
    if not 0 <= idx < len(pairs):
        raise PanelUserError("Список устарел, откройте его заново.")
    return pairs[idx][0]


def _pairs(squads: list[dict]) -> list[list[str]]:
    return [[str(s["uuid"]), str(s.get("name") or s["uuid"])] for s in squads if s.get("uuid")]


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_kpu_ext"), IsAdminFilter(), flags=POPUP)
@loud
async def handle_external_menu(
    callback: CallbackQuery, callback_data: AdminUserEditorCallback, session: AsyncSession, state: FSMContext
):
    key, base = await _base(session, callback_data)
    user, squads = await service.load(session, key, "external")
    pairs = _pairs(squads)
    await state.update_data({PREFIX + "ext": pairs})
    await _edit(callback, views.external_text(), views.external_kb(base, pairs, user.get("externalSquadUuid")))


@router.callback_query(PanelUserCallback.filter(F.action == "ext_set"), IsAdminFilter(), flags=POPUP)
@loud
async def handle_external_set(
    callback: CallbackQuery, callback_data: PanelUserCallback, session: AsyncSession, state: FSMContext
):
    squad = None if callback_data.idx == -1 else await _picked(state, "ext", callback_data.idx)
    key = await _key(session, callback_data.user_id, callback_data.key_ref)
    await service.set_field(session, key, "externalSquadUuid", squad)
    await _show_key_card(callback, session, state, callback_data.user_id, callback_data.key_ref, SAVED)


@router.callback_query(AdminUserEditorCallback.filter(F.action == "users_kpu_int"), IsAdminFilter(), flags=POPUP)
@loud
async def handle_internal_menu(
    callback: CallbackQuery, callback_data: AdminUserEditorCallback, session: AsyncSession, state: FSMContext
):
    key, base = await _base(session, callback_data)
    user, squads = await service.load(session, key, "internal")
    pairs, selected = _pairs(squads), service.internal_squad_uuids(user)
    await state.update_data({PREFIX + "int": pairs, PREFIX + "int_sel": selected})
    await _edit(callback, views.internal_text(), views.internal_kb(base, pairs, selected))


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
    key = await _key(session, callback_data.user_id, callback_data.key_ref)
    await service.set_field(session, key, service.INTERNAL, data[PREFIX + "int_sel"])
    await _show_key_card(callback, session, state, callback_data.user_id, callback_data.key_ref, SAVED)


@router.callback_query(AdminUserEditorCallback.filter(F.action.in_(ASK_ACTIONS)), IsAdminFilter(), flags=POPUP)
@loud
async def handle_field_ask(
    callback: CallbackQuery, callback_data: AdminUserEditorCallback, session: AsyncSession, state: FSMContext
):
    key, base = await _base(session, callback_data)
    field = ASK_ACTIONS[callback_data.action]
    await state.set_state(PanelUserState.waiting_value)
    await state.update_data({PREFIX + "field": field, PREFIX + "base": base.pack()})
    if field == TRANSFER:
        await _edit(callback, views.transfer_ask_text(key.email, _owner_label(key)), views.back_kb(base))
        return
    user, _ = await service.load(session, key)
    await _edit(callback, views.ask_text(field, user), views.ask_kb(base, field))


@router.callback_query(
    PanelUserCallback.filter((F.action == "clr") & F.field.in_(FIELDS)), IsAdminFilter(), flags=POPUP
)
@loud
async def handle_field_clear(
    callback: CallbackQuery, callback_data: PanelUserCallback, session: AsyncSession, state: FSMContext
):
    key = await _key(session, callback_data.user_id, callback_data.key_ref)
    await service.set_field(session, key, views.FIELD_PROMPTS[callback_data.field][2], None)
    await _show_key_card(callback, session, state, callback_data.user_id, callback_data.key_ref, SAVED)


@router.callback_query(PanelUserCallback.filter(F.action == "back"), IsAdminFilter())
async def handle_panel_back(
    callback: CallbackQuery, callback_data: PanelUserCallback, session: AsyncSession, state: FSMContext
):
    # Через свой обработчик, а не сразу в карточку: иначе ожидание ввода пережило бы уход с экрана.
    await _show_key_card(callback, session, state, callback_data.user_id, callback_data.key_ref)


@router.message(StateFilter(PanelUserState.waiting_value), IsAdminFilter())
@loud
async def handle_field_input(message: Message, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    field, packed = data.get(PREFIX + "field"), data.get(PREFIX + "base")
    if (field not in FIELDS and field != TRANSFER) or not packed:
        await _forget(state)
        raise PanelUserError("Ввод устарел, откройте поле заново.")
    base = PanelUserCallback.unpack(packed)
    parse = validate.parse_telegram_id if field == TRANSFER else FIELDS[field]
    try:
        value = parse(message.text or "")
    except ValueError as error:
        await message.answer(str(error))
        return
    key = await _key(session, base.user_id, base.key_ref)
    if field != TRANSFER:
        # Ждать ввода дальше нельзя: при сбое панели следующий текст админа (поиск клиента) лёг бы в поле.
        await _forget(state)
        await service.set_field(session, key, views.FIELD_PROMPTS[field][2], value)
        await _show_key_card(message, session, state, base.user_id, base.key_ref)
        return
    target = await get_user_by_tg_id(session, value)
    if target is None:
        await message.answer("❌ Клиента с таким TG ID нет в базе бота. Пришлите другой.")
        return
    if int(target.id) == int(key.user_id):
        await message.answer("❌ Подписка уже у этого клиента. Пришлите другой TG ID.")
        return
    await state.set_state(None)
    await state.update_data({PREFIX + "dst": int(target.id)})
    await message.answer(
        views.transfer_confirm_text(key.email, _owner_label(key), str(value)),
        reply_markup=views.transfer_confirm_kb(base),
    )


@router.callback_query(PanelUserCallback.filter(F.action == "mv_ok"), IsAdminFilter(), flags=POPUP)
@loud
async def handle_transfer_confirm(
    callback: CallbackQuery, callback_data: PanelUserCallback, session: AsyncSession, state: FSMContext
):
    dst = (await state.get_data()).get(PREFIX + "dst")
    if dst is None:
        raise KeyTransferError("Перенос устарел, начните заново.")
    key = await _key(session, callback_data.user_id, callback_data.key_ref)
    panel_ok = await transfer_key(session, key, dst, actor_tg_id=callback.from_user.id)
    if panel_ok:
        await callback.answer("✅ Подписка перенесена")
    else:
        await callback.answer(
            "⚠️ Подписка перенесена в базе, но панель не обновила TG ID и почту. Поправьте их в панели.",
            show_alert=True,
        )
    await _show_key_card(callback, session, state, UserId(dst), callback_data.key_ref)
