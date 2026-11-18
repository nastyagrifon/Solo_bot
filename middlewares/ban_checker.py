from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import pytz

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.redis_cache import cache_delete, cache_get, cache_key, cache_set
from database import async_session_maker
from database.access.resolution import TelegramId, resolve_uid_cached
from database.models import ManualBan, PreBan
from logger import logger
from settings.cache_config import BAN_CACHE_TTL_SEC
from settings.config import ADMIN_ID, SUPPORT_CHAT_URL


TZ = pytz.timezone("Europe/Moscow")
_BAN_CACHE_TTL = BAN_CACHE_TTL_SEC
# Клиенту никогда не показывается внутренняя причина (ManualBan.reason), только публичная или этот текст.
DEFAULT_PUBLIC_BAN_REASON = "не указана"


async def invalidate_ban_cache(tg_id: int) -> None:
    """Сбросить кэш статуса бана после добавления/снятия бана."""
    await cache_delete(cache_key("ban_status", tg_id))
    await cache_delete(cache_key("preban_status", tg_id))


class BanCheckerMiddleware(BaseMiddleware):
    """Проверка банов."""

    async def _load_ban_info(
        self, session: AsyncSession, tg_id: int, *, owner_uid: int | None = None
    ) -> dict[str, Any] | None:
        if owner_uid is None:
            owner_uid = await resolve_uid_cached(session, TelegramId(tg_id))
        query = (
            select(ManualBan.reason, ManualBan.public_reason, ManualBan.until)
            .where(
                ManualBan.user_id == owner_uid,
                (ManualBan.until.is_(None)) | (ManualBan.until > datetime.now(timezone.utc)),
            )
            .limit(1)
        )
        result = await session.execute(query)
        row = result.first()
        if row:
            reason, public_reason, until = row
            shadow = reason == "shadow"
            await cache_set(
                cache_key("ban_status", tg_id),
                {
                    "user_id": owner_uid,
                    "has_ban": True,
                    "shadow": shadow,
                    "public_reason": public_reason,
                    "until": until.isoformat() if until else None,
                },
                _BAN_CACHE_TTL,
            )
            return {"shadow": shadow, "public_reason": public_reason, "until": until}

        await cache_set(cache_key("ban_status", tg_id), {"user_id": owner_uid, "has_ban": False}, _BAN_CACHE_TTL)
        return None

    async def _cached_ban_info(self, session: AsyncSession, tg_id: int) -> dict[str, Any] | None:
        owner_uid = await resolve_uid_cached(session, TelegramId(tg_id))
        if owner_uid is None:
            return None
        cached = await cache_get(cache_key("ban_status", tg_id))
        # запись старого формата (без shadow) хранила внутреннюю причину — перечитываем из базы
        if not isinstance(cached, dict) or cached.get("user_id") != owner_uid or (
            cached.get("has_ban") and "shadow" not in cached
        ):
            return await self._load_ban_info(session, tg_id, owner_uid=owner_uid)
        if not cached.get("has_ban"):
            return None
        until_raw = cached.get("until")
        until_parsed = None
        if isinstance(until_raw, str):
            try:
                until_parsed = datetime.fromisoformat(until_raw)
            except ValueError:
                until_parsed = None
        if until_parsed is not None and until_parsed < datetime.now(timezone.utc):
            await cache_delete(cache_key("ban_status", tg_id))
            return None
        return {
            "shadow": bool(cached.get("shadow")),
            "public_reason": cached.get("public_reason"),
            "until": until_parsed,
        }

    async def _cached_preban_info(self, session: AsyncSession, tg_id: int) -> dict[str, Any] | None:
        """Предбан по tg id: срабатывает и для тех, кого ещё нет в базе. В кэш — только публичная причина."""
        key = cache_key("preban_status", tg_id)
        cached = await cache_get(key)
        if not isinstance(cached, dict):
            row = (await session.execute(select(PreBan.public_reason).where(PreBan.tg_id == tg_id).limit(1))).first()
            cached = {"has_ban": row is not None, "public_reason": row[0] if row else None}
            await cache_set(key, cached, _BAN_CACHE_TTL)
        if not cached.get("has_ban"):
            return None
        return {"shadow": False, "public_reason": cached.get("public_reason"), "until": None}

    async def _ban_info(self, session: AsyncSession, tg_id: int) -> dict[str, Any] | None:
        return await self._cached_ban_info(session, tg_id) or await self._cached_preban_info(session, tg_id)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_id = None
        obj = None

        if isinstance(event, Update):
            if event.message:
                tg_id = event.message.from_user.id
                obj = event.message
            elif event.callback_query:
                tg_id = event.callback_query.from_user.id
                obj = event.callback_query
        elif isinstance(event, Message | CallbackQuery):
            tg_id = event.from_user.id
            obj = event

        if tg_id is None:
            return await handler(event, data)

        session = data.get("session")
        if session is not None and getattr(session, "execute", None) is not None:
            ban_info = await self._ban_info(session, tg_id)
        else:
            async with async_session_maker() as short_session:
                ban_info = await self._ban_info(short_session, tg_id)
                await short_session.commit()

        if not ban_info:
            return await handler(event, data)

        reason = ban_info["public_reason"] or DEFAULT_PUBLIC_BAN_REASON
        until = ban_info["until"]

        admin_ids = set(ADMIN_ID) if isinstance(ADMIN_ID, list | tuple) else {ADMIN_ID}
        if tg_id in admin_ids:
            return await handler(event, data)

        if ban_info["shadow"]:
            logger.info(f"[BanChecker] Теневой бан: пользователь {tg_id} — действия игнорируются.")
            return

        if until:
            until_local = until.astimezone(TZ).strftime("%Y-%m-%d %H:%M")
            text_html = (
                f"🚫 Вы заблокированы до <b>{until_local}</b> по МСК.\n"
                f"📄 Причина: <i>{reason}</i>\n\n"
                f"Если вы считаете, что это ошибка, обратитесь в поддержку: {SUPPORT_CHAT_URL}"
            )
            text_plain = (
                f"🚫 Вы заблокированы до {until_local} по МСК.\n"
                f"📄 Причина: {reason}\n\n"
                f"Если вы считаете, что это ошибка, обратитесь в поддержку: {SUPPORT_CHAT_URL}"
            )
        else:
            text_html = (
                f"🚫 Вы заблокированы <b>навсегда</b>.\n"
                f"📄 Причина: <i>{reason}</i>\n\n"
                f"Если вы считаете, что это ошибка, обратитесь в поддержку: {SUPPORT_CHAT_URL}"
            )
            text_plain = (
                f"🚫 Вы заблокированы навсегда.\n"
                f"📄 Причина: {reason}\n\n"
                f"Если вы считаете, что это ошибка, обратитесь в поддержку: {SUPPORT_CHAT_URL}"
            )

        if isinstance(obj, Message):
            await obj.answer(text_html, parse_mode="HTML")
        elif isinstance(obj, CallbackQuery):
            alert_text = text_plain if len(text_plain) <= 200 else text_plain[:197] + "..."
            await obj.answer(alert_text, show_alert=True)
        return
