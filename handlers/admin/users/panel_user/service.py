"""Атомарные действия с пользователем панели, стоящим за ключом подписки.

Одно действие — один смысл: прочитать, перечислить, поменять одно поле.
Панель спрашиваем на сервере КЛЮЧА, без fallback_any: при нескольких панелях
запрос иначе ушёл бы на произвольную и правил бы чужого пользователя.
База бота здесь не трогается: меняются только поля, которые живут в панели.
"""

from dataclasses import dataclass

from panels.remnawave_runtime import invalidate_remnawave_profile, with_remnawave_api


TIMEOUT_SEC = 10.0


class PanelUserError(Exception):
    """Панель не ответила или не приняла правку. Текст показывается админу."""


@dataclass(frozen=True)
class KeyTarget:
    """Чем ключ адресует своего пользователя в панели."""

    server_id: str
    client_id: str
    email: str
    subscription_url: str | None

    @classmethod
    def of(cls, key) -> "KeyTarget":
        return cls(str(key.server_id or ""), key.client_id, key.email, key.remnawave_link or key.key)


# --- чистые помощники --------------------------------------------------------


def internal_squad_uuids(user: dict) -> list[str]:
    """Сквады пользователя приходят и объектами, и голыми uuid."""
    squads = user.get("activeInternalSquads") or []
    return [str(s.get("uuid") if isinstance(s, dict) else s) for s in squads]


def toggle(selected: list[str], uuid: str) -> list[str]:
    return [u for u in selected if u != uuid] if uuid in selected else [*selected, uuid]


# --- действия с панелью ------------------------------------------------------


async def _run(session, target: KeyTarget, operation, failure: str):
    result = await with_remnawave_api(
        session,
        target.server_id,
        operation,
        timeout_sec=TIMEOUT_SEC,
        client_id=target.client_id,
        username=target.email,
        subscription_url=target.subscription_url,
    )
    if result is None:
        raise PanelUserError(failure)
    return result


async def load_user(session, target: KeyTarget) -> dict:
    async def op(api):
        return await api.get_user_by_uuid(target.client_id, username=target.email) or None

    return await _run(session, target, op, "Панель не ответила: пользователь не прочитан")


async def list_external_squads(session, target: KeyTarget) -> list[dict]:
    async def op(api):
        # Готового метода у клиента панели нет; путь без /api — он уже в базе клиента.
        response = await api._request("GET", "/external-squads")
        if response.status_code != 200:
            return None
        return (response.json().get("response") or {}).get("externalSquads")

    return await _run(session, target, op, "Панель не отдала список внешних сквадов")


async def list_internal_squads(session, target: KeyTarget) -> list[dict]:
    async def op(api):
        return await api.get_internal_squads()

    return await _run(session, target, op, "Панель не отдала список внутренних сквадов")


def _saved_value(user: dict, field: str):
    if field == "activeInternalSquads":
        return sorted(internal_squad_uuids(user))
    return user.get(field)


async def _patch_user(session, target: KeyTarget, field: str, value) -> dict:
    """Меняет ровно одно поле и сверяет его по ответу панели."""

    async def op(api):
        user = await api.get_user_by_uuid(target.client_id, username=target.email)
        if not user:
            return None
        response = await api._request("PATCH", "/users", json={"uuid": user["uuid"], field: value})
        if response.status_code != 200:
            return None
        return response.json().get("response")

    saved = await _run(session, target, op, "Панель не приняла правку")
    expected = sorted(value) if field == "activeInternalSquads" else value
    if _saved_value(saved, field) != expected:
        raise PanelUserError("Панель ответила, но поле не изменилось")
    await invalidate_remnawave_profile(session, target.server_id, str(target.client_id))
    return saved


async def set_external_squad(session, target: KeyTarget, squad_uuid: str | None) -> dict:
    return await _patch_user(session, target, "externalSquadUuid", squad_uuid)


async def set_internal_squads(session, target: KeyTarget, squad_uuids: list[str]) -> dict:
    return await _patch_user(session, target, "activeInternalSquads", list(squad_uuids))


async def set_telegram_id(session, target: KeyTarget, tg_id: int | None) -> dict:
    return await _patch_user(session, target, "telegramId", tg_id)


async def set_tag(session, target: KeyTarget, tag: str | None) -> dict:
    return await _patch_user(session, target, "tag", tag)


async def set_description(session, target: KeyTarget, text: str | None) -> dict:
    return await _patch_user(session, target, "description", text)


async def clear_devices(session, target: KeyTarget) -> bool:
    async def op(api):
        if await api.clear_all_hwid_devices(target.client_id, username=target.email) is False:
            return None
        # Перечитываем: ответ «удалено» без пустого списка не считаем успехом.
        devices = await api.get_user_hwid_devices(target.client_id, username=target.email)
        return True if devices == [] else None

    await _run(session, target, op, "Панель не подтвердила отвязку устройств")
    await invalidate_remnawave_profile(session, target.server_id, str(target.client_id))
    return True
