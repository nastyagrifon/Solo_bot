import time

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from database.db import async_session_maker
from database.models import Admin
from settings.config import ADMIN_ID

from .permissions import SUPERADMIN_ROLE, normalize_permissions, normalize_role


_ADMIN_CACHE: dict[int, tuple[float, bool, bool, frozenset[str], str]] = {}
_ADMIN_CACHE_TTL = 60


def _get_cached_admin(user_id: int) -> tuple[bool, bool, frozenset[str], str] | None:
    now = time.time()
    entry = _ADMIN_CACHE.get(user_id)
    if entry and entry[0] > now:
        return entry[1], entry[2], entry[3], entry[4]
    return None


def _set_cached_admin(user_id: int, is_admin: bool, is_superadmin: bool,
                      permissions: frozenset[str], role: str = "") -> None:
    # Роль лежит здесь же: иначе рубеж модулей ходил бы за ней в базу на
    # каждое нажатие, мимо этого кэша.
    _ADMIN_CACHE[user_id] = (time.time() + _ADMIN_CACHE_TTL, is_admin,
                             is_superadmin, permissions, role)


def invalidate_admin_cache(user_id: int | None = None) -> None:
    if user_id is None:
        _ADMIN_CACHE.clear()
    else:
        _ADMIN_CACHE.pop(user_id, None)


async def _resolve_admin(user_id: int) -> tuple[bool, bool, frozenset[str], str]:
    cached = _get_cached_admin(user_id)
    if cached is not None:
        return cached

    try:
        async with async_session_maker() as session:
            admin = (await session.execute(select(Admin).where(Admin.tg_id == user_id))).scalar_one_or_none()
            admin_ids = (ADMIN_ID,) if isinstance(ADMIN_ID, int) else ADMIN_ID
            role = ""
            if user_id in admin_ids:
                is_admin = True
                is_super = True
                perms = frozenset()
                role = SUPERADMIN_ROLE
            elif admin is not None:
                # Суперадмин — ровно одна роль. Прежде считалось «любая, кроме
                # moderator», поэтому опечатка в поле или новая роль открывали
                # полный доступ.
                is_admin = True
                role = normalize_role(admin.role)
                is_super = role == SUPERADMIN_ROLE
                perms = frozenset(normalize_permissions(admin.permissions))
            else:
                is_admin = False
                is_super = False
                perms = frozenset()
            _set_cached_admin(user_id, is_admin, is_super, perms, role)
            await session.commit()
            return is_admin, is_super, perms, role
    except Exception:
        return False, False, frozenset(), ""


class IsAdminFilter(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        if not event.from_user:
            return False
        is_admin, _, _, _ = await _resolve_admin(event.from_user.id)
        return is_admin


class HasPermission(BaseFilter):
    def __init__(self, *permissions: str, require_all: bool = False) -> None:
        if not permissions:
            raise ValueError("HasPermission requires at least one permission id")
        self.permissions = tuple(permissions)
        self.require_all = require_all

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        if not event.from_user:
            return False
        is_admin, is_super, perms, _ = await _resolve_admin(event.from_user.id)
        if not is_admin:
            return False
        if is_super:
            return True
        if self.require_all:
            return all(p in perms for p in self.permissions)
        return any(p in perms for p in self.permissions)


async def get_admin_context(user_id: int) -> tuple[bool, bool, frozenset[str]]:
    """Прежний договор: админ, суперадмин, права. Роль — `get_admin_role`."""
    is_admin, is_super, perms, _ = await _resolve_admin(user_id)
    return is_admin, is_super, perms


async def get_admin_role(user_id: int) -> str:
    """Каноническая роль из того же кэша, без второго похода в базу."""
    return (await _resolve_admin(user_id))[3]
