"""Перенос одной подписки на другой профиль бота по решению администратора.

Сначала база одной транзакцией, потом панель. Панель после коммита не
откатывает базу: подписка уже у нового владельца, а сбой панели возвращается
вызывающему, чтобы админ увидел его и поправил, а не узнал из тикета.
"""

from sqlalchemy import delete, select, update

from audit import safe_record_audit_event
from database.access.resolution import UserId, panel_identity_fields
from database.keys import (
    invalidate_key_details,
    invalidate_key_email,
    invalidate_keys_list,
    lock_owned_key_for_operation,
)
from database.models import (
    Key,
    KeyTrafficHistory,
    KeyTrafficHourly,
    Notification,
    SubscriptionEvent,
    User,
    YooKassaSubscription,
)
from database.yookassa_autopay import cancel_subscription
from logger import PANEL_REMNA, logger
from middlewares.session import operation_session
from panels.remnawave_runtime import invalidate_remnawave_profile, with_remnawave_api


class KeyTransferError(Exception):
    """Перенос не начат: текст для админа."""


async def _move_rows(session, src_uid: int, dst: User, client_id: str, email: str) -> None:
    """Строки ЭТОГО ключа. Платежи не трогаем: это история того, кто платил."""
    dst_tg = dst.tg_id
    await session.execute(
        update(Key).where(Key.user_id == src_uid, Key.client_id == client_id).values(user_id=dst.id, tg_id=dst_tg)
    )
    # Состояние уведомлений о трафике едет за ключом каскадом по внешнему ключу (user_id, client_id).
    await session.execute(
        update(SubscriptionEvent)
        .where(SubscriptionEvent.user_id == src_uid, SubscriptionEvent.client_id == client_id)
        .values(user_id=dst.id, tg_id=dst_tg)
    )
    for model in (KeyTrafficHistory, KeyTrafficHourly):
        await session.execute(update(model).where(model.client_id == client_id).values(tg_id=dst_tg))
    # Отметки напоминаний ключа именуются по его почте; без переноса новому владельцу ушло бы повторное.
    own = Notification.notification_type.startswith(f"{email}_", autoescape=True)
    await session.execute(delete(Notification).where(Notification.user_id == dst.id, own))
    await session.execute(
        update(Notification).where(Notification.user_id == src_uid, own).values(user_id=dst.id, tg_id=dst_tg)
    )
    # Автоплатёж списывает с карты прежнего владельца: за чужую подписку платить он не должен.
    autopay_ids = await session.scalars(
        select(YooKassaSubscription.id).where(
            YooKassaSubscription.user_id == src_uid,
            YooKassaSubscription.client_id == client_id,
            YooKassaSubscription.is_active.is_(True),
        )
    )
    for subscription_id in autopay_ids.all():
        await cancel_subscription(session, src_uid, subscription_id)


async def _update_panel(session, key_obj, dst_uid: int) -> bool:
    panel_tg, panel_email = await panel_identity_fields(session, UserId(dst_uid))
    # Почту прежнего владельца стираем явно: иначе она осталась бы на чужой подписке.
    fields = {"telegramId": panel_tg, "email": panel_email}

    async def op(api):
        user = await api.get_user_by_uuid(key_obj.client_id, username=key_obj.email)
        if not user:
            return None
        response = await api._request("PATCH", "/users", json={"uuid": user["uuid"], **fields})
        if response.status_code != 200:
            return None
        saved = response.json().get("response") or {}
        return all(saved.get(name) == value for name, value in fields.items()) or None

    server_ref = str(key_obj.server_id or "")
    ok = await with_remnawave_api(
        session,
        server_ref,
        op,
        timeout_sec=10.0,
        client_id=key_obj.client_id,
        username=key_obj.email,
        subscription_url=key_obj.remnawave_link or key_obj.key,
    )
    if ok:
        await invalidate_remnawave_profile(session, server_ref, str(key_obj.client_id))
    return bool(ok)


async def transfer_key(session, key_obj, dst_user_id: int, *, actor_tg_id: int | None) -> bool:
    """Переносит подписку; True — панель тоже обновлена, False — только база.

    KeyTransferError — ничего не изменено.
    """
    src_uid = int(key_obj.user_id)
    if int(dst_user_id) == src_uid:
        raise KeyTransferError("Подписка уже у этого клиента.")
    client_id, email = key_obj.client_id, key_obj.email
    async with operation_session(session) as operation:
        locked = await lock_owned_key_for_operation(operation, UserId(src_uid), client_id, email)
        if locked is None:
            raise KeyTransferError("Данные устарели. Откройте подписку заново.")
        dst = await operation.scalar(select(User).where(User.id == int(dst_user_id)).with_for_update())
        if dst is None:
            raise KeyTransferError("Клиент-получатель пропал из базы.")
        await _move_rows(operation, src_uid, dst, client_id, email)
        await safe_record_audit_event(
            operation,
            event_type="admin_key_transferred",
            channel="telegram",
            path_or_handler="admin:key_transfer",
            actor_tg_id=actor_tg_id,
            entity_type="key",
            entity_id=client_id,
            metadata={"from_user_id": src_uid, "to_user_id": int(dst.id), "email": email},
        )
        await invalidate_keys_list(operation, UserId(src_uid))
        await invalidate_keys_list(operation, UserId(dst.id))
        await invalidate_key_details(email)
        await invalidate_key_email(client_id)
    logger.info(f"[KeyTransfer] {client_id}: user {src_uid} → {dst_user_id}")
    try:
        panel_ok = await _update_panel(session, locked, int(dst_user_id))
    except Exception as e:
        logger.error(f"{PANEL_REMNA} [KeyTransfer] панель не обновлена после переноса {client_id}: {e}")
        return False
    if not panel_ok:
        logger.error(f"{PANEL_REMNA} [KeyTransfer] панель не подтвердила TG ID/почту после переноса {client_id}")
    return panel_ok
