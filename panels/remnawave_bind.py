"""Привязка подписки ключа перед вызовами клиента панели по client_id.

Клиент панели находит пользователя по ссылке подписки, привязанной через
``bind_subscription``. У части ключей client_id бота не совпадает с id в
панели, и без привязки пользователь не находится: вызов молча не доходит.
Вместо правки каждого места вызова оборачиваем методы на самом классе —
так покрыты и ядро, и модули, которые создают ``RemnawaveAPI`` сами.
"""

from __future__ import annotations

import functools
import inspect
import threading

from logger import logger


_METHODS = (
    "get_user_hwid_devices",
    "delete_user_hwid_device",
    "clear_all_hwid_devices",
    "get_user_by_uuid",
    "update_user",
    "delete_user",
    "disable_user",
    "enable_user",
    "revoke_user_subscription",
    "reset_user_traffic",
    "bulk_set_expiry",
    "resolve_user_id",
)
_FLAG = "_bind_shim_installed"
_SEEN = "_bind_shim_seen"


def _call_ids(name: str, args: tuple, kwargs: dict) -> list[str]:
    if name == "bulk_set_expiry":
        items = args[0] if args else kwargs.get("items") or ()
        ids = []
        for item in items or ():
            if isinstance(item, (tuple, list)) and item:
                ids.append(item[0])
            elif isinstance(item, dict):
                ids.append(item.get("uuid") or item.get("client_id"))
        return [str(i) for i in ids if i]
    cid = args[0] if args else kwargs.get("uuid") or kwargs.get("client_id")
    return [str(cid)] if isinstance(cid, str) and cid else []


def _seen(api) -> dict:
    """id -> True (привязан) / False (ключа нет); на экземпляр клиента."""
    try:
        return api.__dict__.setdefault(_SEEN, {})
    except AttributeError:
        return {}


async def _lookup(ids: list[str]) -> dict[str, tuple[str | None, str | None]]:
    from sqlalchemy import select

    from database import async_session_maker
    from database.models import Key

    async with async_session_maker() as session:
        rows = await session.execute(
            select(Key.client_id, Key.email, Key.remnawave_link, Key.key).where(Key.client_id.in_(ids))
        )
        found: dict[str, tuple[str | None, str | None]] = {}
        for client_id, email, remnawave_link, key in rows.all():
            found.setdefault(str(client_id), (email, remnawave_link or key))
        return found


async def _ensure_bound(api, ids: list[str]) -> None:
    bind = getattr(api, "bind_subscription", None)
    if not bind or not ids:
        return
    # Пул соединений базы живёт в цикле главного потока; в цикле run_io из
    # другого потока его трогать нельзя — там привязку делает with_remnawave_api.
    if threading.current_thread() is not threading.main_thread():
        return
    seen = _seen(api)
    missing = list(dict.fromkeys(i for i in ids if i not in seen))
    if not missing:
        return
    found = await _lookup(missing)
    for cid in missing:
        email, link = found.get(cid, (None, None))
        if link:
            bind(cid, email, link)
        seen[cid] = bool(link)


def _wrap(name: str, orig):
    @functools.wraps(orig)
    async def wrapper(self, *args, **kwargs):
        try:
            await _ensure_bound(self, _call_ids(name, args, kwargs))
        except Exception as error:
            logger.debug(f"[Remnawave] привязка подписки перед {name} не удалась: {error}")
        result = orig(self, *args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    return wrapper


def _wrap_bind(orig):
    @functools.wraps(orig)
    def bind_subscription(self, client_id, *args, **kwargs):
        result = orig(self, client_id, *args, **kwargs)
        _seen(self)[str(client_id)] = True
        return result

    return bind_subscription


def install(cls=None) -> bool:
    """Оборачивает методы класса клиента панели; повторный вызов ничего не делает."""
    if cls is None:
        from panels.remnawave import RemnawaveAPI as cls
    if getattr(cls, _FLAG, False):
        return False
    if not hasattr(cls, "bind_subscription"):
        return False
    try:
        setattr(cls, _FLAG, True)
        cls.bind_subscription = _wrap_bind(cls.bind_subscription)
        for name in _METHODS:
            orig = getattr(cls, name, None)
            if orig is not None:
                setattr(cls, name, _wrap(name, orig))
    except (AttributeError, TypeError) as error:
        logger.warning(f"[Remnawave] обёртка привязки подписки не установлена: {error}")
        return False
    return True
