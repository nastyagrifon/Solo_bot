import csv
import io

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database import delete_user_data
from database.access.resolution import TelegramId, UserId, resolve_user_optional
from database.models import BlockedUser, Key, ManualBan, User
from database.users import add_user
from filters.admin import HasPermission, IsAdminFilter
from filters.permissions import PERM_MANAGEMENT
from logger import logger
from middlewares.ban_checker import invalidate_ban_cache

from ..panel.headers import menu_text, quote, section, wrap_text
from ..panel.keyboard import AdminPanelCallback
from .keyboard import (
    build_bans_kb,
    build_blocked_users_kb,
    build_manual_bans_kb,
    build_shadow_bans_kb,
)


router = Router()
router.callback_query.filter(HasPermission(PERM_MANAGEMENT))
router.message.filter(HasPermission(PERM_MANAGEMENT))


class PreemptiveBanStates(StatesGroup):
    waiting_for_preemptive_ids = State()


class ManualBansStates(StatesGroup):
    waiting_for_import_file = State()
    waiting_for_unban_ids = State()


IMPORT_MAX_BYTES = 1024 * 1024
IMPORT_BAD_LINES_SHOWN = 10
_CHUNK = 1000
# tg id бана: у бана клиента — из users, у бана без клиента — свой (зеркало у старых строк бывает выдуманным)
BAN_TG_ID = case((ManualBan.user_id.is_(None), ManualBan.tg_id), else_=User.tg_id)
_ACTIVE_BAN = or_(ManualBan.until.is_(None), ManualBan.until > func.now())


@router.callback_query(AdminPanelCallback.filter(F.action == "bans"), IsAdminFilter())
async def handle_bans(callback_query: CallbackQuery):
    text_ = menu_text(
        "Баны",
        "Кто и как отрезан от бота.",
        section(
            "📋 Виды",
            "Забанившие: закрыли бота сами",
            "Теневые: бот их игнорирует",
            "Ручные: забанены из админки или импортом",
        ),
    )
    await callback_query.message.edit_text(text=text_, reply_markup=build_bans_kb())


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_blocked_menu"), IsAdminFilter())
async def handle_blocked_users_menu(callback_query: CallbackQuery):
    text_ = menu_text(
        "Забанившие бота",
        "Выберите действие.",
        quote("Клиенты, которые заблокировали бота сами или удалили чат."),
    )
    await callback_query.message.edit_text(text=text_, reply_markup=build_blocked_users_kb())


def get_shadow_bans_menu_text() -> str:
    return menu_text(
        "Теневые баны",
        "Выберите действие.",
        quote("Бот молча игнорирует таких клиентов — уведомления о бане они не получают."),
        quote(
            "Добавить можно сразу пачкой: пришлите список Telegram ID, "
            "по одному в строке.\n<code>123456789\n987654321</code>"
        ),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_shadow_menu"), IsAdminFilter())
async def handle_shadow_bans_menu(callback_query: CallbackQuery):
    await callback_query.message.edit_text(
        text=wrap_text(get_shadow_bans_menu_text()), reply_markup=build_shadow_bans_kb()
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_manual_menu"), IsAdminFilter())
async def handle_manual_bans_menu(callback_query: CallbackQuery):
    text_ = menu_text(
        "Ручные баны",
        "Выберите действие.",
        quote("Клиенты, которых вы забанили из админки, и tg id из импорта — в том числе ещё не пришедшие в бота."),
    )
    await callback_query.message.edit_text(text=text_, reply_markup=build_manual_bans_kb())


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_export"), IsAdminFilter())
async def handle_bans_export(callback_query: CallbackQuery, session: AsyncSession):
    kb = build_blocked_users_kb()
    try:
        result = await session.execute(
            select(User.tg_id).join(BlockedUser, BlockedUser.user_id == User.id).where(User.tg_id.isnot(None))
        )
        banned_users = [row[0] for row in result.all()]

        csv_output = io.StringIO()
        writer = csv.writer(csv_output)
        writer.writerow(["tg_id"])

        for tg_id in banned_users:
            writer.writerow([tg_id])

        csv_output.seek(0)
        document = BufferedInputFile(file=csv_output.getvalue().encode("utf-8"), filename="banned_users.csv")

        await callback_query.message.answer_document(
            document=document,
            caption="📥 Экспорт пользователей, заблокировавших бота (CSV)",
        )
    except Exception as e:
        await callback_query.message.answer(
            text=menu_text("Баны", f"❌ Не удалось выгрузить: {e}"),
            reply_markup=kb,
        )


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_delete_banned"), IsAdminFilter())
async def handle_bans_delete_banned(callback_query: CallbackQuery, session: AsyncSession):
    kb = build_blocked_users_kb()
    try:
        stmt = (
            select(BlockedUser.user_id).outerjoin(Key, BlockedUser.user_id == Key.user_id).where(Key.user_id.is_(None))
        )
        result = await session.execute(stmt)
        blocked_ids = [row[0] for row in result.all()]

        if not blocked_ids:
            await callback_query.message.answer(
                text=menu_text("Баны", "Никто не блокировал бота."),
                reply_markup=kb,
            )
            return

        for user_id in blocked_ids:
            await delete_user_data(session, UserId(user_id))

        await callback_query.message.answer(
            text=menu_text("Баны", f"🗑️ Удалены данные о {len(blocked_ids)} клиентах и связанных записях."),
            reply_markup=kb,
        )
    except Exception as e:
        await callback_query.message.answer(
            text=menu_text("Баны", f"❌ Не удалось удалить записи: {e}"),
            reply_markup=kb,
        )


@router.callback_query(AdminPanelCallback.filter(F.action == "shadow_bans_export"), IsAdminFilter())
async def handle_shadow_bans_export(callback_query: CallbackQuery, session: AsyncSession):
    kb = build_shadow_bans_kb()
    try:
        result = await session.execute(
            select(User.tg_id, ManualBan.user_id, ManualBan.banned_at, ManualBan.banned_by, ManualBan.until)
            .select_from(ManualBan)
            .join(User, ManualBan.user_id == User.id)
            .where(ManualBan.reason == "shadow")
        )
        rows = result.all()

        csv_output = io.StringIO()
        writer = csv.writer(csv_output)
        writer.writerow(["tg_id", "banned_at", "banned_by", "until", "user_id"])

        for row in rows:
            writer.writerow([row.tg_id, row.banned_at, row.banned_by, row.until, row.user_id])

        csv_output.seek(0)
        document = BufferedInputFile(file=csv_output.getvalue().encode("utf-8"), filename="shadow_bans.csv")

        await callback_query.message.answer_document(
            document=document,
            caption="📥 Экспорт теневых банов (CSV)",
        )
    except Exception as e:
        await callback_query.message.answer(
            text=menu_text("Баны", f"❌ Ошибка при экспорте: {e}"),
            reply_markup=kb,
        )


@router.callback_query(AdminPanelCallback.filter(F.action == "manual_bans_export"), IsAdminFilter())
async def handle_manual_bans_export(callback_query: CallbackQuery, session: AsyncSession):
    kb = build_manual_bans_kb()
    try:
        result = await session.execute(
            select(
                BAN_TG_ID.label("tg_id"),
                ManualBan.user_id,
                ManualBan.banned_at,
                ManualBan.reason,
                ManualBan.public_reason,
                ManualBan.until,
                ManualBan.banned_by,
            )
            .select_from(ManualBan)
            .outerjoin(User, ManualBan.user_id == User.id)
            .where(or_(ManualBan.reason != "shadow", ManualBan.reason.is_(None)), _ACTIVE_BAN)
        )
        rows = result.all()

        csv_output = io.StringIO()
        writer = csv.writer(csv_output)
        writer.writerow(["tg_id", "banned_at", "reason", "public_reason", "until", "banned_by", "user_id"])

        for row in rows:
            writer.writerow([
                row.tg_id, row.banned_at, row.reason, row.public_reason, row.until, row.banned_by, row.user_id
            ])

        csv_output.seek(0)
        document = BufferedInputFile(file=csv_output.getvalue().encode("utf-8"), filename="manual_bans.csv")

        await callback_query.message.answer_document(
            document=document,
            caption="📥 Активные баны (CSV) — этот же файл принимает импорт",
        )
    except Exception as e:
        await callback_query.message.answer(
            text=menu_text("Баны", f"❌ Ошибка при экспорте: {e}"),
            reply_markup=kb,
        )


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_clear_blocked"), IsAdminFilter())
async def handle_clear_blocked_users(callback_query: CallbackQuery, session: AsyncSession):
    kb = build_blocked_users_kb()
    try:
        count_result = await session.execute(select(func.count()).select_from(BlockedUser))
        total_count = count_result.scalar() or 0

        if total_count == 0:
            await callback_query.message.answer(
                text=menu_text("Баны", "Никто не блокировал бота."),
                reply_markup=kb,
            )
            return

        await session.execute(delete(BlockedUser))

        await callback_query.message.answer(
            text=menu_text("Баны", f"🗑️ Очищено {total_count} записей."),
            reply_markup=kb,
        )
        logger.info(f"[BANS] Очищено {total_count} записей из blocked_users")
    except Exception as e:
        logger.error(f"[BANS] Ошибка при очистке blocked_users: {e}")
        await callback_query.message.answer(
            text=menu_text("Баны", f"❌ Не удалось очистить список: {e}"),
            reply_markup=kb,
        )


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_clear_shadow"), IsAdminFilter())
async def handle_clear_shadow_bans(callback_query: CallbackQuery, session: AsyncSession):
    kb = build_shadow_bans_kb()
    try:
        count_result = await session.execute(
            select(func.count()).select_from(ManualBan).where(ManualBan.reason == "shadow")
        )
        total_count = count_result.scalar() or 0

        if total_count == 0:
            await callback_query.message.answer(
                text=menu_text("Баны", "📂 Нет теневых банов для очистки."),
                reply_markup=kb,
            )
            return

        tg_ids_result = await session.execute(
            select(User.tg_id)
            .select_from(ManualBan)
            .join(User, ManualBan.user_id == User.id)
            .where(ManualBan.reason == "shadow")
        )
        tg_to_invalidate = [r[0] for r in tg_ids_result.all() if r[0] is not None]
        await session.execute(delete(ManualBan).where(ManualBan.reason == "shadow"))
        for tid in tg_to_invalidate:
            await invalidate_ban_cache(tid)

        await callback_query.message.answer(
            text=menu_text("Баны", f"🗑️ Очищено {total_count} записей теневых банов из базы данных."),
            reply_markup=kb,
        )
        logger.info(f"[BANS] Очищено {total_count} записей теневых банов из manual_bans")
    except Exception as e:
        logger.error(f"[BANS] Ошибка при очистке теневых банов: {e}")
        await callback_query.message.answer(
            text=menu_text("Баны", f"❌ Ошибка при очистке теневых банов: {e}"),
            reply_markup=kb,
        )


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_clear_manual"), IsAdminFilter())
async def handle_clear_manual_bans(callback_query: CallbackQuery, session: AsyncSession):
    kb = build_manual_bans_kb()
    try:
        count_result = await session.execute(
            select(func.count())
            .select_from(ManualBan)
            .where(or_(ManualBan.reason != "shadow", ManualBan.reason.is_(None)))
        )
        total_count = count_result.scalar() or 0

        if total_count == 0:
            await callback_query.message.answer(
                text=menu_text("Баны", "📂 Нет ручных банов для очистки."),
                reply_markup=kb,
            )
            return

        tg_ids_result = await session.execute(
            select(BAN_TG_ID)
            .select_from(ManualBan)
            .outerjoin(User, ManualBan.user_id == User.id)
            .where(or_(ManualBan.reason != "shadow", ManualBan.reason.is_(None)))
        )
        tg_to_invalidate = [r[0] for r in tg_ids_result.all() if r[0] is not None]
        await session.execute(delete(ManualBan).where(or_(ManualBan.reason != "shadow", ManualBan.reason.is_(None))))
        for tid in tg_to_invalidate:
            await invalidate_ban_cache(tid)

        await callback_query.message.answer(
            text=menu_text("Баны", f"🗑️ Очищено {total_count} записей ручных банов из базы данных."),
            reply_markup=kb,
        )
        logger.info(f"[BANS] Очищено {total_count} записей ручных банов из manual_bans")
    except Exception as e:
        logger.error(f"[BANS] Ошибка при очистке ручных банов: {e}")
        await callback_query.message.answer(
            text=menu_text("Баны", f"❌ Ошибка при очистке ручных банов: {e}"),
            reply_markup=kb,
        )


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_preemptive"), IsAdminFilter())
async def handle_preemptive_ban_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PreemptiveBanStates.waiting_for_preemptive_ids)

    builder = InlineKeyboardBuilder()
    builder.button(
        text="❌ Отмена",
        callback_data=AdminPanelCallback(action="bans_cancel_preemptive").pack(),
    )
    await callback.message.edit_text(
        menu_text(
            "Теневой бан", "Пришлите Telegram ID, по одному в строке.", section("📥 Пример", "123456789", "987654321")
        ),
        reply_markup=builder.as_markup(),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "bans_cancel_preemptive"), IsAdminFilter())
async def handle_cancel_preemptive_ban(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(text=wrap_text(get_shadow_bans_menu_text()), reply_markup=build_shadow_bans_kb())


@router.message(PreemptiveBanStates.waiting_for_preemptive_ids, IsAdminFilter())
async def handle_preemptive_ids_input(message: Message, state: FSMContext, session: AsyncSession):
    lines = message.text.strip().splitlines()
    tg_ids = set()

    for line in lines:
        line = line.strip()
        if line.isdigit():
            tg_ids.add(int(line))

    if not tg_ids:
        await message.answer(menu_text("Баны", "❌ Не найдено ни одного корректного Telegram ID."))
        return

    now = datetime.now(timezone.utc)

    rows = []
    cache_tg_ids = []
    for raw_tg in tg_ids:
        u = await resolve_user_optional(session, TelegramId(raw_tg))
        if u is None:
            await add_user(session, raw_tg)
            await session.flush()
            u = await resolve_user_optional(session, TelegramId(raw_tg))
        if u is None:
            continue
        rows.append({
            "user_id": u.id,
            "tg_id": u.tg_id,
            "reason": "shadow",
            "banned_by": message.from_user.id,
            "until": None,
            "banned_at": now,
        })
        if u.tg_id is not None:
            cache_tg_ids.append(u.tg_id)

    if not rows:
        await message.answer(menu_text("Баны", "❌ Ни один Telegram ID не совпал с клиентом."))
        await state.clear()
        return

    ins = pg_insert(ManualBan).values(rows)
    stmt = ins.on_conflict_do_update(
        index_elements=[ManualBan.user_id],
        set_={
            "tg_id": ins.excluded.tg_id,
            "reason": "shadow",
            "public_reason": None,
            "until": None,
            "banned_by": message.from_user.id,
            "banned_at": now,
        },
    )

    await session.execute(stmt)
    for tid in cache_tg_ids:
        await invalidate_ban_cache(tid)

    await message.answer(
        menu_text("Баны", f"✅ Успешно добавлено в теневой бан: <b>{len(rows)}</b> клиентов."),
        reply_markup=build_shadow_bans_kb(),
    )
    await state.clear()


def _parse_tg_id(raw: str) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw.isdigit() and 0 < int(raw) < 2**63 else None


def parse_bans_file(content: str) -> tuple[dict[int, dict], list[int]]:
    """Разбор файла импорта: свой экспорт CSV или строки «<tg_id> #причина».

    Возвращает баны по tg id (повтор — берётся первая строка) и номера плохих строк.
    Причина из текстового файла — внутренняя, публичной нет.
    """
    bans: dict[int, dict] = {}
    bad: list[int] = []
    lines = content.splitlines()
    first = next((line.strip() for line in lines if line.strip()), "")
    if first.split(",")[0].strip() == "tg_id":
        start = next(i for i, line in enumerate(lines) if line.strip())
        reader = csv.DictReader(lines[start:])
        now = datetime.now(timezone.utc)
        for row in reader:
            line_no = start + reader.line_num
            if not any((v or "").strip() for v in row.values() if isinstance(v, str)):
                continue
            tg_id = _parse_tg_id(row.get("tg_id"))
            until = None
            try:
                if (row.get("until") or "").strip():
                    until = datetime.fromisoformat(row["until"].strip())
                    until = until if until.tzinfo else until.replace(tzinfo=timezone.utc)
            except ValueError:
                tg_id = None
            if tg_id is None or (until is not None and until <= now):
                bad.append(line_no)
                continue
            bans.setdefault(
                tg_id,
                {
                    "reason": (row.get("reason") or "").strip() or None,
                    "public_reason": (row.get("public_reason") or "").strip() or None,
                    "until": until,
                },
            )
        return bans, bad

    for line_no, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        head, _, comment = line.partition("#")
        tg_id = _parse_tg_id(head)
        if tg_id is None:
            bad.append(line_no)
            continue
        bans.setdefault(tg_id, {"reason": comment.strip() or None, "public_reason": None, "until": None})
    return bans, bad


async def import_bans(session: AsyncSession, bans: dict[int, dict], banned_by: int) -> tuple[int, int]:
    """Дополняет баны: существующий бан по tg id (клиента или без клиента) не трогает.

    Возвращает (добавлено, уже было).
    """
    added = already = 0
    now = datetime.now(timezone.utc)
    tg_ids = list(bans)
    for i in range(0, len(tg_ids), _CHUNK):
        chunk = tg_ids[i : i + _CHUNK]
        users = dict((await session.execute(select(User.tg_id, User.id).where(User.tg_id.in_(chunk)))).all())
        banned = {
            row[0]
            for row in (
                await session.execute(
                    select(BAN_TG_ID)
                    .select_from(ManualBan)
                    .outerjoin(User, ManualBan.user_id == User.id)
                    .where(
                        or_(
                            ManualBan.user_id.in_(list(users.values()) or [-1]),
                            ManualBan.user_id.is_(None) & ManualBan.tg_id.in_(chunk),
                        )
                    )
                )
            ).all()
        }
        rows = [
            {
                "user_id": users.get(tg_id),
                "tg_id": tg_id,
                "reason": bans[tg_id]["reason"],
                "public_reason": bans[tg_id]["public_reason"],
                "until": bans[tg_id]["until"],
                "banned_by": banned_by,
                "banned_at": now,
            }
            for tg_id in chunk
            if tg_id not in banned
        ]
        already += len(chunk) - len(rows)
        if not rows:
            continue
        # без цели конфликта: гонка с параллельным баном тоже не перезаписывает
        result = await session.execute(pg_insert(ManualBan).values(rows).on_conflict_do_nothing())
        inserted = result.rowcount if result.rowcount is not None and result.rowcount >= 0 else len(rows)
        added += inserted
        already += len(rows) - inserted
        for row in rows:
            await invalidate_ban_cache(row["tg_id"])
    return added, already


def _cancel_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data=AdminPanelCallback(action="manual_bans_cancel").pack())
    return builder.as_markup()


@router.callback_query(AdminPanelCallback.filter(F.action == "manual_bans_cancel"), IsAdminFilter())
async def handle_manual_bans_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await handle_manual_bans_menu(callback)


@router.callback_query(AdminPanelCallback.filter(F.action == "manual_bans_import"), IsAdminFilter())
async def handle_manual_bans_import_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ManualBansStates.waiting_for_import_file)
    await callback.message.edit_text(
        menu_text(
            "Импорт банов",
            "Пришлите файл. Импорт только добавляет: существующие баны не меняются.",
            section(
                "📄 Форматы",
                "CSV из «Экспорт» этого раздела",
                "или текст: <code>&lt;tg_id&gt; #причина</code>, по одному в строке",
            ),
            quote("Причина из текста — внутренняя, клиент видит нейтральный текст. Бан навсегда."),
        ),
        reply_markup=_cancel_kb(),
    )


@router.message(ManualBansStates.waiting_for_import_file, IsAdminFilter())
async def handle_manual_bans_import_file(message: Message, state: FSMContext, session: AsyncSession):
    document = message.document
    if document is None:
        await message.answer(menu_text("Импорт банов", "Пришлите файл документом."), reply_markup=_cancel_kb())
        return
    limit_text = f"Файл больше {IMPORT_MAX_BYTES // 1024} КБ."
    if (document.file_size or 0) > IMPORT_MAX_BYTES:
        await message.answer(menu_text("Импорт банов", limit_text), reply_markup=_cancel_kb())
        return
    buffer = io.BytesIO()
    await message.bot.download(document, destination=buffer)
    if buffer.tell() > IMPORT_MAX_BYTES:
        await message.answer(menu_text("Импорт банов", limit_text), reply_markup=_cancel_kb())
        return
    try:
        content = buffer.getvalue().decode("utf-8-sig")
    except UnicodeDecodeError:
        await message.answer(menu_text("Импорт банов", "Нужен текстовый файл в UTF-8."), reply_markup=_cancel_kb())
        return

    bans, bad = parse_bans_file(content)
    added, already = await import_bans(session, bans, message.from_user.id)
    await state.clear()
    lines = [f"Добавлено: {added}", f"Уже было: {already}", f"Пропущено строк: {len(bad)}"]
    if bad:
        shown = ", ".join(map(str, bad[:IMPORT_BAD_LINES_SHOWN]))
        lines.append(f"Плохие строки: {shown}" + (" …" if len(bad) > IMPORT_BAD_LINES_SHOWN else ""))
    logger.info(
        f"[BANS] Импорт банов от {message.from_user.id}: добавлено {added}, уже было {already}, плохих {len(bad)}"
    )
    await message.answer(menu_text("Импорт банов", section("📊 Итог", *lines)), reply_markup=build_manual_bans_kb())


@router.callback_query(AdminPanelCallback.filter(F.action == "manual_bans_unban"), IsAdminFilter())
async def handle_manual_bans_unban_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ManualBansStates.waiting_for_unban_ids)
    await callback.message.edit_text(
        menu_text(
            "Разбан по tg id",
            "Пришлите Telegram ID, по одному в строке.",
            quote("Снимается бан клиента с этим tg id и бан по tg id без клиента."),
        ),
        reply_markup=_cancel_kb(),
    )


@router.message(ManualBansStates.waiting_for_unban_ids, IsAdminFilter())
async def handle_manual_bans_unban_input(message: Message, state: FSMContext, session: AsyncSession):
    tg_ids = {tg for tg in map(_parse_tg_id, (message.text or "").splitlines()) if tg is not None}
    if not tg_ids:
        await message.answer(
            menu_text("Баны", "❌ Не найдено ни одного корректного Telegram ID."), reply_markup=_cancel_kb()
        )
        return
    user_ids = (await session.execute(select(User.id).where(User.tg_id.in_(tg_ids)))).scalars().all()
    result = await session.execute(
        delete(ManualBan).where(
            or_(
                ManualBan.user_id.in_(list(user_ids) or [-1]),
                ManualBan.user_id.is_(None) & ManualBan.tg_id.in_(tg_ids),
            )
        )
    )
    for tg_id in tg_ids:
        await invalidate_ban_cache(tg_id)
    await state.clear()
    await message.answer(
        menu_text("Разбан по tg id", f"🔓 Снято банов: <b>{result.rowcount or 0}</b>"),
        reply_markup=build_manual_bans_kb(),
    )
