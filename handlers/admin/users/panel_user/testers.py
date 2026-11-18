"""Сквады и тег тестировщиков: задаются один раз и ставятся на все их подписки Remnawave.

Без этого каждого тестировщика настраивали в панели руками, подписку за подпиской.
Настройка лежит в MANAGEMENT_CONFIG — туда же, где остальные правки управления из админки.
"""

import html

from sqlalchemy import select

from core.settings.management_config import MANAGEMENT_CONFIG, update_management_config
from database.access.resolution import get_user_by_tg_id
from database.models import Admin, Key
from filters.permissions import TESTER_ROLE, normalize_role
from logger import logger
from panels.remnawave_runtime import with_remnawave_api
from services.subscription_keys import resolve_remnawave_server_ref

from . import service


TESTER_TAG = "TESTER"
EXTERNAL = "TESTER_EXTERNAL_SQUAD"
INTERNAL = "TESTER_INTERNAL_SQUADS"


def configured() -> tuple[bool, str | None, list[str]]:
    """(внешний задан?, внешний uuid или None, внутренние uuid)."""
    return EXTERNAL in MANAGEMENT_CONFIG, MANAGEMENT_CONFIG.get(EXTERNAL), list(MANAGEMENT_CONFIG.get(INTERNAL) or [])


async def save_external(session, uuid: str | None) -> None:
    await update_management_config(session, {**MANAGEMENT_CONFIG, EXTERNAL: uuid})


async def save_internal(session, uuids: list[str]) -> None:
    await update_management_config(session, {**MANAGEMENT_CONFIG, INTERNAL: list(uuids)})


async def panel_squads(session) -> tuple[list[dict], list[dict]]:
    """Внешние и внутренние сквады панели одним заходом."""

    async def op(api):
        external = await service._squads(api, "external")
        internal = await service._squads(api, "internal")
        return None if external is None or internal is None else (external, internal)

    # shortcut: настройка общая на бота, списки берём с первой панели Remnawave; при нескольких панелях
    # со своими сквадами подписки чужой панели уйдут в «не удалось» — тогда нужна настройка на панель.
    result = await with_remnawave_api(session, "", op, fallback_any=True, timeout_sec=service.TIMEOUT_SEC)
    if result is None:
        raise service.PanelUserError("Панель не ответила: сквады не прочитаны")
    return result


def tester_values() -> dict:
    """Поля панели тестировщика. Не заданное в настройке не трогаем."""
    has_external, external, internal = configured()
    values = {"tag": TESTER_TAG}
    if has_external:
        values["externalSquadUuid"] = external
    # Пустой список отрезал бы тестировщика от всех нод — значит «не задано».
    if internal:
        values[service.INTERNAL] = internal
    return values


async def _remnawave_keys(session, tg_id: int) -> list:
    user = await get_user_by_tg_id(session, tg_id)
    if user is None:
        return []
    keys = (await session.execute(select(Key).where(Key.user_id == user.id))).scalars().all()
    return [key for key in keys if await resolve_remnawave_server_ref(session, key.server_id or "")]


async def apply(session, tg_id: int, tester: bool) -> tuple[int, list[str], list[str]]:
    """Ставит (tester=True) или снимает поля тестировщика.

    Итог: сколько подписок изменено, почты неудачных, почты с чужим тегом, оставленным как есть.
    Чужой тег не перезаписываем и не снимаем: его ставили не мы и для другого. При снятии роли
    убираем только тег TESTER: сквады после тестов правят руками.
    """
    values = tester_values()
    done, failed, kept = 0, [], []
    for key in await _remnawave_keys(session, tg_id):
        try:
            user, _ = await service.load(session, key)
            tag = user.get("tag")
            foreign = tag not in (None, "", TESTER_TAG)
            if tester:
                fields = {k: v for k, v in values.items() if k != "tag"} if foreign else values
            else:
                fields = {"tag": None} if tag == TESTER_TAG else {}
            if foreign and tester:
                kept.append(key.email)
            if not fields:
                continue
            await service.set_fields(session, key, fields)
            done += 1
        except Exception as error:
            # Одна сбойная подписка не должна оставить остальные ненастроенными.
            logger.warning(f"[Testers] {key.email}: {error}")
            failed.append(key.email)
    return done, failed, kept


async def apply_all(session) -> tuple[int, list[str], list[str]]:
    """Поля тестировщика на подписки всех админов с ролью tester."""
    done, failed, kept = 0, [], []
    for tg_id, role in (await session.execute(select(Admin.tg_id, Admin.role))).all():
        if normalize_role(role) == TESTER_ROLE:
            changed, bad, foreign = await apply(session, tg_id, True)
            done, failed, kept = done + changed, failed + bad, kept + foreign
    return done, failed, kept


def _emails(emails: list[str]) -> str:
    return ", ".join(html.escape(str(email)) for email in emails)


def report(done: int, failed: list[str], kept: list[str] = ()) -> str:
    text = f"Подписок изменено: {done}."
    if failed:
        text += "\nНе удалось: " + _emails(failed)
    if kept:
        text += f"\nЧужой тег оставлен, {TESTER_TAG} не поставлен: " + _emails(kept)
    return text
