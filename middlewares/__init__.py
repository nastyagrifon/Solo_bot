from collections.abc import Iterable

from aiogram import BaseMiddleware, Dispatcher

from core.bootstrap import MODES_CONFIG
from middlewares.ban_checker import BanCheckerMiddleware
from middlewares.subscription import SubscriptionMiddleware

from .actor import ActorMiddleware
from .admin import AdminMiddleware
from .answer import CallbackAnswerMiddleware, EarlyCallbackAnswerMiddleware
from .concurrency import ConcurrencyLimiterMiddleware
from .delete_commands import DeleteCommandMiddleware
from .direct_start_blocker import DirectStartBlockerMiddleware
from .loggings import LoggingMiddleware
from .maintenance import MaintenanceModeMiddleware
from .runtime_config_sync import RuntimeConfigSyncMiddleware
from .session import SessionMiddleware
from .throttling import ThrottlingMiddleware
from .user import UserMiddleware



def register_middleware(
    dispatcher: Dispatcher,
    middlewares: Iterable[BaseMiddleware | type[BaseMiddleware]] | None = None,
    exclude: Iterable[str] | None = None,
    pool=None,  # передаёт закрытое ядро, здесь не используется
    sessionmaker=None,
) -> None:
    exclude_set = set(exclude or [])

    flag_by_name = {
        "runtime_config_sync": "RUNTIME_CONFIG_SYNC_MIDDLEWARE_ENABLED",
        "concurrency": "CONCURRENCY_MIDDLEWARE_ENABLED",
        "subscription": "SUBSCRIPTION_MIDDLEWARE_ENABLED",
        "session": "SESSION_MIDDLEWARE_ENABLED",
        "direct_start_blocker": "DIRECT_START_BLOCKER_MIDDLEWARE_ENABLED",
        "ban_checker": "BAN_CHECKER_MIDDLEWARE_ENABLED",
        "admin": "ADMIN_MIDDLEWARE_ENABLED",
        "maintenance": "MAINTENANCE_MIDDLEWARE_ENABLED",
        "logging": "LOGGING_MIDDLEWARE_ENABLED",
        "throttling": "THROTTLING_MIDDLEWARE_ENABLED",
        "user": "USER_MIDDLEWARE_ENABLED",
        "actor": "ACTOR_MIDDLEWARE_ENABLED",
        "answer": "ANSWER_MIDDLEWARE_ENABLED",
        "delete_commands": "DELETE_COMMANDS_MIDDLEWARE_ENABLED",
    }

    def middleware_enabled(name: str) -> bool:
        if name in exclude_set:
            return False
        flag_name = flag_by_name.get(name)
        if not flag_name:
            return True
        return bool(MODES_CONFIG.get(flag_name, True))

    dispatcher.update.outer_middleware(EarlyCallbackAnswerMiddleware())

    if middleware_enabled("runtime_config_sync"):
        dispatcher.update.outer_middleware(RuntimeConfigSyncMiddleware())
    if sessionmaker and middleware_enabled("concurrency"):
        dispatcher.update.outer_middleware(ConcurrencyLimiterMiddleware())
    if sessionmaker and middleware_enabled("session"):
        dispatcher.update.outer_middleware(SessionMiddleware(sessionmaker))
    if middleware_enabled("ban_checker"):
        dispatcher.update.outer_middleware(BanCheckerMiddleware())
    if middleware_enabled("direct_start_blocker"):
        dispatcher.update.outer_middleware(DirectStartBlockerMiddleware())
    if middleware_enabled("admin"):
        dispatcher.update.outer_middleware(AdminMiddleware())
    if middleware_enabled("maintenance"):
        dispatcher.update.outer_middleware(MaintenanceModeMiddleware())
    if middleware_enabled("subscription"):
        dispatcher.update.outer_middleware(SubscriptionMiddleware())

    if middlewares is None:
        available_middlewares = {
            "logging": LoggingMiddleware(sessionmaker) if sessionmaker else LoggingMiddleware(),
            "throttling": ThrottlingMiddleware(),
            "user": UserMiddleware(),
            "actor": ActorMiddleware(),
        }
        middlewares = [mw for name, mw in available_middlewares.items() if middleware_enabled(name)]
    else:
        middlewares = [mw() if isinstance(mw, type) else mw for mw in middlewares]

    handlers = [dispatcher.message, dispatcher.callback_query, dispatcher.inline_query]
    for middleware in middlewares:
        for h in handlers:
            h.outer_middleware(middleware)

    if middleware_enabled("answer"):
        dispatcher.callback_query.middleware(CallbackAnswerMiddleware())

    if middleware_enabled("delete_commands"):
        dispatcher.message.outer_middleware(DeleteCommandMiddleware())
