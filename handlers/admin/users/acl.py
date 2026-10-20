"""Рубеж правки внутри карточки клиента.

Право `users` пускает в карточку, но внутри неё есть и просмотр, и правка
подписки, и деньги. Один фильтр на роутер их не различает, а ставить фильтр на
каждый из полусотни обработчиков значит держать правку в полусотне мест.

Поэтому проверка одна — inner middleware роутера: она вызывается после фильтров
роутера и перед обработчиком, и уже знает, какой обработчик выбран
(`data["handler"]`). Чтобы отказать, достаточно не позвать `handler`.

Перечислены только ЧИТАЮЩИЕ обработчики. Всё, чего в списке нет, считается
правкой: обработчик, появившийся в ядре после нас, потребует больше прав, а не
меньше. Цена — связь по имени функции, поэтому `tests/test_admin_acl.py`
сверяет списки с живым кодом и краснеет, когда имя исчезло.
"""

from filters.permissions import PERM_BALANCE, PERM_USERS_WRITE

# Модуль целиком про деньги: начисления, списания, компенсации. Кто не вправе
# их делать, не должен и открывать экран списания.
MONEY_MODULE = "users_balance"

# Исключения внутри денежного модуля: просмотр истории и выгрузка.
MONEY_READ_ONLY = frozenset({
    "handle_balance_page",
    "handle_balance_export",
})

# Читающие обработчики карточки.
READ_ONLY = frozenset({
    "handle_user_audit", "handle_user_audit_page",
    "handle_users_gifts", "handle_gifts_page",
    "handle_hwid_menu", "handle_hwid_page",
    "handle_search_user", "handle_user_data_input", "handle_search_page",
    "handle_users_export_referrals", "handle_users_editor",
    "handle_users_site", "handle_users_site_tab",
    "handle_cancel_user_message",
    "handle_hosts_list", "handle_host_show",
    "handle_user_traffic", "handle_user_sub_history",
    "handle_key_edit", "handle_back_to_key_menu", "handle_back_to_group",
})


def required_permission(module: str, name: str) -> str | None:
    """Какое право нужно обработчику. None — хватает права на чтение."""
    if module.rsplit(".", 1)[-1] == MONEY_MODULE:
        return None if name in MONEY_READ_ONLY else PERM_BALANCE
    return None if name in READ_ONLY else PERM_USERS_WRITE


async def users_acl(handler, event, data):
    # Импорт внутри: `filters.admin` тянет базу, а правила прав должны
    # проверяться без неё.
    from filters.admin import get_admin_context

    user = getattr(event, "from_user", None)
    if user is None:
        return None

    _, is_super, perms = await get_admin_context(user.id)
    if is_super:
        return await handler(event, data)

    callback = getattr(data.get("handler"), "callback", None)
    need = required_permission(
        getattr(callback, "__module__", ""), getattr(callback, "__name__", "")
    )
    if need is None or need in perms:
        return await handler(event, data)

    # Молчать нельзя: немой отказ выглядит сломанной кнопкой — человек видит
    # часики и не понимает, что произошло.
    answer = getattr(event, "answer", None)
    if answer is not None:
        await answer("Недостаточно прав для этого действия", show_alert=True)
    return None


def install(router) -> None:
    """Навесить рубеж на роутер и все вложенные в него.

    Inner middleware роутера действует только на ЕГО обработчики: по
    `Router._propagate_event` дочерние роутеры получают событие отдельным
    вызовом и прогоняют собственные middleware. У роутера карточки своих
    обработчиков нет вовсе, поэтому обходим дерево. Звать после
    `include_router`, иначе `sub_routers` ещё пуст.
    """
    router.callback_query.middleware(users_acl)
    router.message.middleware(users_acl)
    for sub in router.sub_routers:
        install(sub)
