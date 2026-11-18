from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.access.resolution import UserId, resolve_user_optional
from database.models import ManualBan
from filters.admin import IsAdminFilter
from middlewares.ban_checker import DEFAULT_PUBLIC_BAN_REASON, invalidate_ban_cache
from settings.buttons import BACK

from ..panel.headers import menu_text, quote
from .keyboard import AdminUserEditorCallback, build_editor_btn, build_editor_kb, build_user_ban_type_kb
from .users_states import BanUserStates


router = Router()


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_ban"),
    IsAdminFilter(),
)
async def handle_user_ban(callback: CallbackQuery, callback_data: AdminUserEditorCallback, state: FSMContext):
    await state.clear()
    await state.update_data(user_id=callback_data.user_id)

    await callback.message.edit_text(
        text=menu_text("Блокировка", "Выберите тип блокировки."),
        reply_markup=build_user_ban_type_kb(callback_data.user_id),
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_ban_forever"),
    IsAdminFilter(),
)
async def handle_ban_forever_start(callback: CallbackQuery, callback_data: AdminUserEditorCallback, state: FSMContext):
    await state.set_state(BanUserStates.waiting_for_forever_reason)
    await state.update_data(user_id=callback_data.user_id)

    kb = InlineKeyboardBuilder()
    kb.row(build_editor_btn(BACK, user_id=callback_data.user_id, edit=True))

    await callback.message.edit_text(
        text=menu_text(
            "Блокировка",
            "✏️ Введите <b>внутреннюю причину</b> постоянной блокировки — её видят только админы "
            "(или <code>-</code>, чтобы пропустить):",
        ),
        reply_markup=kb.as_markup(),
    )


async def _save_ban(session: AsyncSession, u, *, reason, public_reason, until, banned_by: int) -> None:
    now = datetime.now(timezone.utc)
    fields = {
        "tg_id": u.tg_id,
        "reason": reason,
        "public_reason": public_reason,
        "until": until,
        "banned_by": banned_by,
        "banned_at": now,
    }
    stmt = (
        pg_insert(ManualBan)
        .values(user_id=u.id, **fields)
        .on_conflict_do_update(index_elements=[ManualBan.user_id], set_=fields)
    )
    await session.execute(stmt)
    if u.tg_id is not None:
        await invalidate_ban_cache(u.tg_id)


def _ban_reasons_text(reason: str | None, public_reason: str | None) -> str:
    lines = []
    if reason:
        lines.append(f"🔒 Внутренняя причина: {reason}")
    lines.append(f"👁 Клиент видит: {public_reason or DEFAULT_PUBLIC_BAN_REASON}")
    return "\n".join(lines)


async def _ask_public_reason(message: Message, state: FSMContext) -> None:
    await state.update_data(reason=message.text.strip())
    await state.set_state(BanUserStates.waiting_for_public_reason)

    user_data = await state.get_data()
    user_id = UserId(user_data["user_id"]) if user_data.get("user_id") is not None else None

    kb = InlineKeyboardBuilder()
    kb.row(build_editor_btn(BACK, user_id=user_id, edit=True))

    await message.answer(
        menu_text(
            "Блокировка",
            "👁 Введите <b>публичную причину</b> — её увидит клиент "
            f"(или <code>-</code>, тогда клиент увидит «{DEFAULT_PUBLIC_BAN_REASON}»):",
        ),
        reply_markup=kb.as_markup(),
    )


@router.message(BanUserStates.waiting_for_forever_reason, IsAdminFilter())
async def handle_ban_forever_reason_input(message: Message, state: FSMContext):
    await state.update_data(forever=True)
    await _ask_public_reason(message, state)


@router.message(BanUserStates.waiting_for_public_reason, IsAdminFilter())
async def handle_ban_public_reason_input(message: Message, state: FSMContext, session: AsyncSession):
    public_reason = message.text.strip()
    if public_reason == "-":
        public_reason = None
    await state.update_data(public_reason=public_reason)

    user_data = await state.get_data()
    user_id = UserId(user_data["user_id"]) if user_data.get("user_id") is not None else None

    if not user_data.get("forever"):
        await state.set_state(BanUserStates.waiting_for_ban_duration)
        kb = InlineKeyboardBuilder()
        kb.row(build_editor_btn(BACK, user_id=user_id, edit=True))
        await message.answer(
            menu_text("Блокировка", "⏳ Срок блокировки в днях. 0 — навсегда."),
            reply_markup=kb.as_markup(),
        )
        return

    reason = user_data.get("reason")
    if reason == "-":
        reason = None

    u = await resolve_user_optional(session, user_id)
    if u is None:
        await message.answer(menu_text("Блокировка", "❌ Клиент не найден."))
        await state.clear()
        return

    await _save_ban(
        session, u, reason=reason, public_reason=public_reason, until=None, banned_by=message.from_user.id
    )
    await state.clear()

    await message.answer(
        text=menu_text(
            "Блокировка",
            f"✅ Клиент <code>{u.tg_id or u.id}</code> забанен навсегда.\n{_ban_reasons_text(reason, public_reason)}",
        ),
        reply_markup=build_editor_kb(user_id, edit=True),
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_ban_temporary"),
    IsAdminFilter(),
)
async def handle_ban_temporary(callback: CallbackQuery, callback_data: AdminUserEditorCallback, state: FSMContext):
    await state.set_state(BanUserStates.waiting_for_reason)
    await state.update_data(user_id=callback_data.user_id)

    kb = InlineKeyboardBuilder()
    kb.row(build_editor_btn(BACK, user_id=callback_data.user_id, edit=True))

    await callback.message.edit_text(
        text=menu_text(
            "Блокировка",
            "✏️ Введите <b>внутреннюю причину</b> временной блокировки — её видят только админы "
            "(или <code>-</code>, чтобы пропустить):",
        ),
        reply_markup=kb.as_markup(),
    )


@router.message(BanUserStates.waiting_for_reason, IsAdminFilter())
async def handle_ban_reason_input(message: Message, state: FSMContext):
    await state.update_data(forever=False)
    await _ask_public_reason(message, state)


@router.message(BanUserStates.waiting_for_ban_duration, IsAdminFilter())
async def handle_ban_duration_input(message: Message, state: FSMContext, session: AsyncSession):
    user_data = await state.get_data()
    user_id = UserId(user_data["user_id"]) if user_data.get("user_id") is not None else None
    reason = user_data.get("reason")
    if reason == "-":
        reason = None

    try:
        days = int(message.text.strip())
        if days < 1:
            await message.answer(menu_text("Блокировка", "❌ Укажите срок минимум в 1 день."))
            return

        until = datetime.now(timezone.utc) + timedelta(days=days)

        u = await resolve_user_optional(session, user_id)
        if u is None:
            await message.answer(menu_text("Блокировка", "❌ Клиент не найден."))
            return

        public_reason = user_data.get("public_reason")
        await _save_ban(
            session, u, reason=reason, public_reason=public_reason, until=until, banned_by=message.from_user.id
        )

        text = menu_text(
            "Клиент забанен",
            f"<code>{u.tg_id or u.id}</code>",
            quote(f"До: <b>{until:%Y-%m-%d %H:%M}</b> UTC\n{_ban_reasons_text(reason, public_reason)}"),
        )

        await message.answer(text=text, reply_markup=build_editor_kb(user_id, edit=True))
    except ValueError:
        await message.answer(menu_text("Блокировка", "❌ Введите корректное число дней."))
    finally:
        await state.clear()


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_ban_shadow"),
    IsAdminFilter(),
    flags={"popup": True},
)
async def handle_ban_shadow(callback: CallbackQuery, callback_data: AdminUserEditorCallback, session: AsyncSession):
    u = await resolve_user_optional(session, callback_data.user_id)
    if u is None:
        await callback.answer("Клиент не найден", show_alert=True)
        return

    await _save_ban(session, u, reason="shadow", public_reason=None, until=None, banned_by=callback.from_user.id)

    await callback.message.edit_text(
        text=menu_text("Блокировка", f"👻 Клиент <code>{u.tg_id or u.id}</code> получил теневой бан."),
        reply_markup=build_editor_kb(callback_data.user_id, edit=True),
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_unban"),
    IsAdminFilter(),
    flags={"popup": True},
)
async def handle_user_unban(
    callback: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    u = await resolve_user_optional(session, callback_data.user_id)
    if u is None:
        await callback.answer("Клиент не найден", show_alert=True)
        return

    await session.execute(delete(ManualBan).where(ManualBan.user_id == u.id))
    if u.tg_id is not None:
        await invalidate_ban_cache(u.tg_id)

    text = menu_text(
        "Блокировка снята",
        f"Клиент <code>{u.tg_id or u.id}</code> снова может пользоваться ботом.",
    )

    await callback.message.edit_text(text=text, reply_markup=build_editor_kb(callback_data.user_id, edit=True))
