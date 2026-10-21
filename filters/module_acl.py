"""Рубеж админских экранов модулей.

Модули фильтруют свои админские экраны одним `IsAdminFilter`, то есть
проверяют факт записи в `admins`, а не роль. С появлением ролей это стало
дырой: сотрудник с доступом «только смотреть» открывал настройки модулей,
рассылки и менеджер модулей.

Править четырнадцать модулей по обработчикам нельзя — они живут отдельными
сборками и обновляются сами. Поэтому рубеж стоит в одном месте: загрузчик
вешает middleware на роутер модуля и его дочерние.

Накрывать роутер модуля целиком нельзя: в нём и клиентские экраны. Админским
считаем тот обработчик, который модуль САМ объявил админским, то есть повесил
`IsAdminFilter`. Так модуль остаётся хозяином своей разметки, а ядро только
сверяет роль.
"""
from __future__ import annotations

from aiogram import Router

from logger import logger

from .admin import IsAdminFilter, get_admin_context, get_admin_role
from .permissions import MODULE_PERMISSIONS, WORKING_ROLES


def _is_admin_handler(handler) -> bool:
    """Модуль объявил обработчик админским?"""
    for item in getattr(handler, "filters", None) or ():
        if isinstance(getattr(item, "callback", None), IsAdminFilter):
            return True
    return False


def make_guard(module_name: str):
    """Middleware для одного модуля: сверяет роль на его админских экранах."""
    need = MODULE_PERMISSIONS.get(module_name)

    async def guard(handler, event, data):
        if not _is_admin_handler(data.get("handler")):
            return await handler(event, data)      # клиентский экран не наш

        user = getattr(event, "from_user", None)
        if user is None:
            logger.warning(f"[ModuleACL] {module_name}: событие без автора")
            return None

        _, is_super, perms = await get_admin_context(user.id)
        if is_super:
            return await handler(event, data)

        if need is not None:
            allowed = need in perms
        else:
            # Роль берётся из того же кэша, что и права: второго похода в
            # базу на каждое нажатие быть не должно.
            allowed = await get_admin_role(user.id) in WORKING_ROLES
        if allowed:
            return await handler(event, data)

        answer = getattr(event, "answer", None)
        if answer is not None:
            await answer("Этот раздел вам не открыт", show_alert=True)
        return None

    return guard


def install(router: Router, module_name: str) -> None:
    """Навесить рубеж на роутер модуля и все вложенные.

    Inner middleware действует только на свои обработчики, поэтому обходим
    дерево. Звать после `include_router`.
    """
    guard = make_guard(module_name)
    router.callback_query.middleware(guard)
    router.message.middleware(guard)
    for sub in router.sub_routers:
        install(sub, module_name)
