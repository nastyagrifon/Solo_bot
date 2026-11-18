import hashlib
import html
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from filters.admin import IsAdminFilter
from panels.remnawave_runtime import (
    invalidate_remnawave_profile,
    resolve_remnawave_api_url,
    with_remnawave_api,
)
from services import hwid_block
from services.users_utils import resolve_admin_key

from ..panel.headers import card, menu_text, section
from .keyboard import AdminUserEditorCallback, build_editor_kb, build_hwid_menu_kb, build_hwid_unbind_all_kb
from .panel_user.service import PanelUserError, clear_devices


router = Router()

DEVICES_PER_PAGE = 10
# 12 hex = 48 бит: два устройства одного ключа не совпадут, а кнопка влезает в 64 байта.
FINGERPRINT_LEN = 12
BLOCK_MARKS = {hwid_block.CLIENT_SOURCE: "🔒 клиент", hwid_block.ADMIN_SOURCE: "🔒 админ"}


def fingerprint(hwid: str) -> str:
    """Отпечаток HWID для кнопки: сам HWID до 191 знака и с двоеточиями."""
    return hashlib.sha256(str(hwid).encode()).hexdigest()[:FINGERPRINT_LEN]


def find_device(devices: list[dict], fp: str) -> tuple[int, dict] | None:
    """(номер, устройство) по отпечатку среди устройств ЭТОГО ключа из панели.

    Чужой HWID так не выбрать: ищем только в списке ключа. Два разных HWID под
    одним отпечатком — None, угадывать нельзя.
    """
    found = {}
    for idx, device in enumerate(devices or []):
        hwid = str((device or {}).get("hwid") or "")
        if hwid and fingerprint(hwid) == fp:
            found.setdefault(hwid, (idx, device))
    return next(iter(found.values())) if len(found) == 1 else None


def blockable(hwid) -> bool:
    """Примет ли прослойка HWID: печатные ASCII без пробелов, 1..191."""
    hwid = str(hwid or "")
    return 1 <= len(hwid) <= 191 and all("!" <= c <= "~" for c in hwid)


def _age(raw, now: datetime | None = None) -> str:
    text = str(raw or "").replace("T", " ").replace("Z", "").split(".")[0][:19]
    try:
        then = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return "—"
    sec = ((now or datetime.now(timezone.utc)) - then).total_seconds()
    if sec < 3600:
        return f"{max(int(sec // 60), 0)} мин"
    if sec < 86400:
        return f"{int(sec // 3600)} ч"
    days = int(sec // 86400)
    return f"{days} дн" if days < 31 else f"{days // 30} мес"


def _app(device: dict) -> str:
    parts = str(device.get("userAgent") or "").strip().replace(" ", "/").split("/")
    version = parts[1] if len(parts) > 1 and parts[1][:1].isdigit() else ""
    return f"{parts[0]} {version}".strip()


def device_line(idx: int, device: dict, blocks: dict | None) -> str:
    platform = " ".join(str(device.get(k) or "") for k in ("platform", "osVersion")).strip()
    parts = [str(device.get("deviceModel") or "—"), platform, _app(device), _age(device.get("updatedAt"))]
    mark = (blocks or {}).get(str(device.get("hwid") or ""))
    if mark:
        parts.append(BLOCK_MARKS.get(mark, "🔒 вручную"))
    return f"{idx}. " + html.escape(" · ".join(p for p in parts if p))


def block_action(device: dict, blocks: dict | None) -> str | None:
    """Кнопка блока у устройства: блок, разблок или ничего.

    Ничего — прослойка недоступна, HWID негоден или блок ручной: его снимают
    на самой прослойке.
    """
    hwid = str(device.get("hwid") or "")
    if blocks is None or not blockable(hwid):
        return None
    mark = blocks.get(hwid)
    if not mark:
        return "users_hwid_bl"
    return "users_hwid_ub" if mark in BLOCK_MARKS else None


async def _tariff_limit(session, key_obj):
    limit = key_obj.current_device_limit or key_obj.selected_device_limit
    if limit is None and key_obj.tariff_id:
        from database.tariffs import get_tariff_by_id

        limit = ((await get_tariff_by_id(session, key_obj.tariff_id)) or {}).get("device_limit")
    return limit


async def _load(session, key_obj):
    """(user_info, devices) из панели сервера ключа; None — нет авторизации."""
    # Устройства спрашиваем у панели ТОГО сервера, где лежит ключ: с
    # fallback_any запрос уходил на произвольную панель, и при нескольких
    # панелях админ видел и удалял чужие устройства.
    async def _fetch(api):
        user_info = await api.get_user_by_uuid(key_obj.client_id, username=key_obj.email)
        devices = await api.get_user_hwid_devices(key_obj.client_id, username=key_obj.email)
        return user_info, devices

    return await with_remnawave_api(
        session,
        str(key_obj.server_id or ""),
        _fetch,
        timeout_sec=8.0,
        client_id=key_obj.client_id,
        username=key_obj.email,
        subscription_url=key_obj.remnawave_link or key_obj.key,
    )


async def _render_admin_devices(
    callback_query: CallbackQuery,
    session: AsyncSession,
    key_ref: str,
    user_id: int,
    page: int,
) -> None:
    key_obj = await resolve_admin_key(session, user_id, key_ref)
    if not key_obj:
        await callback_query.message.edit_text(
            menu_text("Устройства", "❌ Подписка не найдена."),
            reply_markup=build_editor_kb(user_id),
        )
        return
    if not await resolve_remnawave_api_url(session, str(key_obj.server_id or "")):
        await callback_query.message.edit_text(
            menu_text("Устройства", "❌ Нет доступного сервера Remnawave."),
            reply_markup=build_editor_kb(user_id),
        )
        return
    result = await _load(session, key_obj)
    if result is None:
        await callback_query.message.edit_text(menu_text("Устройства", "❌ Ошибка авторизации в Remnawave."))
        return
    user_info, devices = result
    if devices is None:
        # Панель не ответила. Пустой список здесь означал бы «устройств нет»,
        # и админ снял бы не ту претензию.
        await callback_query.answer("Панель не ответила, список устройств недоступен", show_alert=True)
        return

    user_info = user_info or {}
    online = "🟢 Онлайн" if user_info.get("isOnline") else "⚪️ Офлайн"
    seen = str(user_info.get("onlineAt") or "")[:16].replace("T", " ") or "—"
    panel_limit = user_info.get("hwidDeviceLimit")
    total = len(devices)
    total_pages = max(1, (total + DEVICES_PER_PAGE - 1) // DEVICES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * DEVICES_PER_PAGE
    page_devices = devices[start : start + DEVICES_PER_PAGE]
    blocks = None
    if page_devices:
        blocks = await hwid_block.status_or_none([str(d["hwid"]) for d in page_devices if blockable(d.get("hwid"))])

    header = section(
        online,
        f"Был в сети: {html.escape(seen)}",
        f"Устройств: {total}",
        f"Лимит тарифа: {await _tariff_limit(session, key_obj) or '—'}",
        f"Лимит панели: {'—' if panel_limit is None else panel_limit}",
    )
    if page_devices:
        lines = [device_line(start + i + 1, d, blocks) for i, d in enumerate(page_devices)]
        body = "<blockquote>" + "\n".join(lines) + "</blockquote>"
    else:
        body = "<blockquote>Привязанных устройств нет</blockquote>"
    rows = [
        (start + i + 1, str(d.get("deviceModel") or "—"), fingerprint(str(d["hwid"])), block_action(d, blocks))
        for i, d in enumerate(page_devices)
        if d.get("hwid")
    ]
    await callback_query.message.edit_text(
        menu_text("Устройства", card(header) + "\n" + body),
        reply_markup=build_hwid_menu_kb(key_ref, user_id, page=page, total_pages=total_pages, rows=rows),
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_hwid_menu"),
    IsAdminFilter(),
)
async def handle_hwid_menu(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    await _render_admin_devices(callback_query, session, str(callback_data.data), callback_data.user_id, 0)


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_hwid_page"),
    IsAdminFilter(),
)
async def handle_hwid_page(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    parts = str(callback_data.data or "").split("|")
    key_ref = parts[0]
    try:
        page = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        page = 0
    await _render_admin_devices(callback_query, session, key_ref, callback_data.user_id, page)


async def _unbind(callback_query, session, key_obj, hwid: str):
    async def _delete(api):
        return await api.delete_user_hwid_device(key_obj.client_id, hwid, username=key_obj.email)

    ok = await with_remnawave_api(
        session,
        str(key_obj.server_id or ""),
        _delete,
        timeout_sec=10.0,
        client_id=key_obj.client_id,
        username=key_obj.email,
        subscription_url=key_obj.remnawave_link or key_obj.key,
    )
    if not ok:
        return "Не удалось отвязать устройство"
    await invalidate_remnawave_profile(session, str(key_obj.server_id or ""), str(key_obj.client_id))
    return "✅ Устройство отвязано."


async def _block(callback_query, session, key_obj, hwid: str):
    await hwid_block.block(hwid, username=key_obj.email, note=f"админ {callback_query.from_user.id}")
    return "🔒 Устройство заблокировано: подписку оно больше не получит."


async def _unblock(callback_query, session, key_obj, hwid: str):
    answer = await hwid_block.unblock(hwid)
    if answer.get("blocked"):
        return "Блок поставлен вручную на прослойке — отсюда его не снять."
    return "🔓 Устройство разблокировано."


async def _device_action(callback_query, callback_data, session, action) -> None:
    """Общий путь кнопок устройства: ключ → свежий список из панели → устройство по отпечатку."""
    key_ref, _, fp = str(callback_data.data or "").partition("|")
    user_id = callback_data.user_id
    key_obj = await resolve_admin_key(session, user_id, key_ref)
    if not key_obj:
        await callback_query.answer("❌ Подписка не найдена.", show_alert=True)
        return
    result = await _load(session, key_obj)
    devices = result[1] if result else None
    if devices is None:
        await callback_query.answer("Панель не ответила, список устройств недоступен", show_alert=True)
        return
    found = find_device(devices, fp)
    if found is None:
        await callback_query.answer("Устройство не найдено", show_alert=True)
        return
    idx, device = found
    try:
        text = await action(callback_query, session, key_obj, str(device["hwid"]))
    except hwid_block.HwidBlockError as error:
        text = f"Прослойка подписки не ответила: {error}"
    await callback_query.answer(text, show_alert=True)
    await _render_admin_devices(callback_query, session, key_ref, user_id, idx // DEVICES_PER_PAGE)


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_hwid_rm"),
    IsAdminFilter(),
    flags={"popup": True},
)
async def handle_hwid_unbind(
    callback_query: CallbackQuery, callback_data: AdminUserEditorCallback, session: AsyncSession
):
    await _device_action(callback_query, callback_data, session, _unbind)


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_hwid_bl"),
    IsAdminFilter(),
    flags={"popup": True},
)
async def handle_hwid_block(
    callback_query: CallbackQuery, callback_data: AdminUserEditorCallback, session: AsyncSession
):
    await _device_action(callback_query, callback_data, session, _block)


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_hwid_ub"),
    IsAdminFilter(),
    flags={"popup": True},
)
async def handle_hwid_unblock(
    callback_query: CallbackQuery, callback_data: AdminUserEditorCallback, session: AsyncSession
):
    await _device_action(callback_query, callback_data, session, _unblock)


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_hwid_unbind_all"),
    IsAdminFilter(),
)
async def handle_hwid_unbind_all(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
):
    key_ref = str(callback_data.data)
    await callback_query.message.edit_text(
        menu_text("Устройства", "Отвязать все устройства этой подписки? Клиенту придётся подключиться заново."),
        reply_markup=build_hwid_unbind_all_kb(key_ref, callback_data.user_id),
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_hwid_unbind_all_ok"),
    IsAdminFilter(),
    flags={"popup": True},
)
async def handle_hwid_unbind_all_ok(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    key_ref = str(callback_data.data)
    key_obj = await resolve_admin_key(session, callback_data.user_id, key_ref)
    if not key_obj:
        await callback_query.answer("❌ Подписка не найдена.", show_alert=True)
        return
    try:
        await clear_devices(session, key_obj)
    except PanelUserError as error:
        await callback_query.answer(f"❌ {error}", show_alert=True)
        return
    await callback_query.answer("✅ Все устройства отвязаны.")
    await _render_admin_devices(callback_query, session, key_ref, callback_data.user_id, 0)
