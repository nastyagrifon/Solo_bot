from aiogram import Router

from filters.admin import HasPermission
from filters.permissions import PERM_USERS
from middlewares.legacy_ref import LegacyUserRefMiddleware


router = Router()
router.callback_query.filter(HasPermission(PERM_USERS))
router.message.filter(HasPermission(PERM_USERS))
router.callback_query.middleware(LegacyUserRefMiddleware())

from . import (
    users_audit,
    users_balance,
    users_bans,
    users_gifts,
    users_hwid,
    users_keys,
    users_manage,
    users_subscription_keys,
    users_tariffs,
)


router.include_router(users_manage.router)
router.include_router(users_audit.router)
router.include_router(users_balance.router)
router.include_router(users_hwid.router)
router.include_router(users_subscription_keys.router)
router.include_router(users_keys.router)
router.include_router(users_bans.router)
router.include_router(users_tariffs.router)
router.include_router(users_gifts.router)

from .acl import install as _install_acl

# Рубеж правки: право `users` пустило в карточку, внутри неё правка подписки
# требует `users_write`, а деньги — `balance`.
_install_acl(router)
