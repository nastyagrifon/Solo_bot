"""Пользователь панели, стоящий за ключом подписки: чтение и правка одного поля.

Панель спрашиваем на сервере КЛЮЧА, без fallback_any: при нескольких панелях
запрос иначе ушёл бы на произвольную и правил бы чужого пользователя.
База бота здесь не трогается: меняются только поля, которые живут в панели.
"""

from panels.remnawave_runtime import invalidate_remnawave_profile, with_remnawave_api


TIMEOUT_SEC = 10.0
INTERNAL = "activeInternalSquads"


class PanelUserError(Exception):
    """Панель не ответила или не приняла правку. Текст показывается админу."""


def internal_squad_uuids(user: dict) -> list[str]:
    """Сквады пользователя приходят и объектами, и голыми uuid."""
    return [str(s.get("uuid") if isinstance(s, dict) else s) for s in user.get(INTERNAL) or []]


def toggle(selected: list[str], uuid: str) -> list[str]:
    return [u for u in selected if u != uuid] if uuid in selected else [*selected, uuid]


async def _run(session, key, operation, failure: str):
    result = await with_remnawave_api(
        session,
        str(key.server_id or ""),
        operation,
        timeout_sec=TIMEOUT_SEC,
        client_id=key.client_id,
        username=key.email,
        subscription_url=key.remnawave_link or key.key,
    )
    if result is None:
        raise PanelUserError(failure)
    return result


async def _external_squads(api):
    # Готового метода у клиента панели нет; путь без /api — он уже в базе клиента.
    response = await api._request("GET", "/external-squads")
    if response.status_code != 200:
        return None
    return (response.json().get("response") or {}).get("externalSquads")


async def load(session, key, squads: str | None = None) -> tuple[dict, list[dict]]:
    """Пользователь и, если нужно, список сквадов ("external"/"internal") — одним заходом в панель."""

    async def op(api):
        user = await api.get_user_by_uuid(key.client_id, username=key.email)
        if not user:
            return None
        if squads is None:
            return user, []
        listed = await (_external_squads(api) if squads == "external" else api.get_internal_squads())
        return None if listed is None else (user, listed)

    return await _run(session, key, op, "Панель не ответила: пользователь не прочитан")


async def set_field(session, key, field: str, value) -> dict:
    """Меняет ровно одно поле пользователя панели и сверяет его по ответу."""

    async def op(api):
        user = await api.get_user_by_uuid(key.client_id, username=key.email)
        if not user:
            return None
        response = await api._request("PATCH", "/users", json={"uuid": user["uuid"], field: value})
        if response.status_code != 200:
            return None
        return response.json().get("response")

    saved = await _run(session, key, op, "Панель не приняла правку")
    if field == INTERNAL:
        ok = sorted(internal_squad_uuids(saved)) == sorted(value)
    else:
        ok = saved.get(field) == value
    if not ok:
        raise PanelUserError("Панель ответила, но поле не изменилось")
    await invalidate_remnawave_profile(session, str(key.server_id or ""), str(key.client_id))
    return saved


async def clear_devices(session, key) -> None:
    async def op(api):
        if await api.clear_all_hwid_devices(key.client_id, username=key.email) is False:
            return None
        # Перечитываем: ответ «удалено» без пустого списка не считаем успехом.
        devices = await api.get_user_hwid_devices(key.client_id, username=key.email)
        return True if devices == [] else None

    await _run(session, key, op, "Панель не подтвердила отвязку устройств")
    await invalidate_remnawave_profile(session, str(key.server_id or ""), str(key.client_id))
