from types import SimpleNamespace
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from core.settings.remnawave_config import (
    REMNAWAVE_CONFIG,
    get_host_rotation_allowed,
    get_node_health_allowed,
    is_host_auto_disable_enabled,
    update_remnawave_config,
)
from database import async_session_maker, get_servers
from logger import logger
from panels import remnawave as remnawave_panel
from settings.config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, REMNAWAVE_TOKEN_LOGIN_ENABLED

from ..panel.headers import card, menu_text, quote, section
from ..panel.keyboard import AdminPanelCallback
from .keyboard import (
    REMNAWAVE_HOSTS_PER_PAGE,
    build_settings_remnawave_health_nodes_kb,
    build_settings_remnawave_hosts_kb,
    build_settings_remnawave_kb,
    build_settings_remnawave_node_kb,
    build_settings_remnawave_rotation_kb,
)


router = Router(name="admin_settings_remnawave")


class RemnawaveSettingsState(StatesGroup):
    waiting_for_node_interval = State()
    waiting_for_rotation_interval = State()


def _node_health_enabled() -> bool:
    return bool(REMNAWAVE_CONFIG.get("NODE_HEALTH_ENABLED", False))


def _auto_disable_enabled() -> bool:
    return is_host_auto_disable_enabled()


def _host_rotation_enabled() -> bool:
    return bool(REMNAWAVE_CONFIG.get("HOST_ROTATION_ENABLED", False))


def _node_interval() -> int:
    return int(REMNAWAVE_CONFIG.get("NODE_HEALTH_INTERVAL_MIN") or 5)


def _rotation_interval() -> int:
    return int(REMNAWAVE_CONFIG.get("HOST_ROTATION_INTERVAL_MIN") or 60)


def _root_text() -> str:
    node_state = "✅ Включён" if _node_health_enabled() else "❌ Выключен"
    rot_state = "✅ Включена" if _host_rotation_enabled() else "❌ Выключена"
    allowed_count = len(get_host_rotation_allowed())
    return menu_text(
        "Remnawave",
        "Фоновые задачи по API панели.",
        card(
            section("🩺 Проверка нод", f"Статус: {node_state}", f"Интервал: {_node_interval()} мин"),
            section(
                "🔀 Ротация хостов",
                f"Статус: {rot_state}",
                f"Интервал: {_rotation_interval()} мин",
                f"Хостов: {allowed_count}",
            ),
        ),
    )


def _node_text() -> str:
    state = "✅ Включена" if _node_health_enabled() else "❌ Выключена"
    auto_state = "✅ Включено" if _auto_disable_enabled() else "❌ Выключено"
    selected_count = len(get_node_health_allowed())
    return menu_text(
        "Проверка нод",
        "Бот следит, какие ноды отвалились.",
        section(
            "🩺 Проверка",
            f"Статус: {state}",
            f"Интервал: {_node_interval()} мин",
            f"Нод: {selected_count if selected_count else 'все'}",
            f"Авто-отключение: {auto_state}",
        ),
        "Когда нода отваливается или возвращается, админам приходит уведомление.",
        quote(
            "Нода замолчала — бот гасит её хосты в панели, чтобы новые подключения "
            "не уходили на мёртвый сервер, и возвращает их, когда нода снова в строю.",
            "Трогает только то, что выключил сам: выключенное вручную останется как есть.",
            "Если отметить конкретные ноды, бот проверит только их — так ноды "
            "авто-балансировки не будут считаться упавшими.",
        ),
    )


def _rotation_text() -> str:
    state = "✅ Включена" if _host_rotation_enabled() else "❌ Выключена"
    allowed = get_host_rotation_allowed()
    return menu_text(
        "Ротация хостов",
        "Свободные хосты поднимаются выше в подписке.",
        section("🔀 Ротация", f"Статус: {state}", f"Интервал: {_rotation_interval()} мин", f"Хостов: {len(allowed)}"),
        quote(
            "Бот считает, сколько людей онлайн на каждой ноде, и двигает наименее нагруженные хосты в начало списка.",
            "Двигаются только отмеченные хосты, остальные стоят на своих местах.",
        ),
    )


def _hosts_text(hosts: list[tuple[str, dict[str, Any]]], allowed: set[str]) -> str:
    if not hosts:
        return menu_text(
            "Хосты Remnawave",
            "Не удалось получить список хостов. Проверьте, что панель доступна, "
            "а у токена есть права на чтение <code>/hosts</code>.",
        )
    total = len(hosts)
    selected = sum(1 for _, h in hosts if str(h.get("uuid")) in allowed)
    return menu_text(
        "Хосты для ротации",
        "Нажмите на строку, чтобы включить или выключить хост.",
        section("🖧 Хосты", f"Всего: {total}", f"В ротации: {selected}"),
        quote("Отмеченные ✅ бот двигает по позициям, глядя на нагрузку их ноды."),
    )


async def _set_cfg(**values: Any) -> None:
    async with async_session_maker() as session:
        await update_remnawave_config(session, {**REMNAWAVE_CONFIG, **values})


async def _fetch_from_panels(method: str, what: str) -> list[tuple[str, dict[str, Any]]]:
    async with async_session_maker() as session:
        servers = await get_servers(session, include_enabled=True)

    seen_panels: set[str] = set()
    result: list[tuple[str, dict[str, Any]]] = []
    for cluster in servers.values():
        for srv in cluster:
            if srv.get("panel_type") != "remnawave":
                continue
            api_url = (srv.get("api_url") or "").strip()
            if not api_url or api_url in seen_panels:
                continue
            seen_panels.add(api_url)
            api = remnawave_panel.RemnawaveAPI(api_url)
            try:
                if not REMNAWAVE_TOKEN_LOGIN_ENABLED:
                    ok = await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD)
                    if not ok:
                        continue
                items = await getattr(api, method)() or []
            except Exception as exc:
                logger.warning("[Remnawave-Admin] Ошибка получения {} с {}: {}", what, api_url, exc)
                continue
            finally:
                try:
                    await api.aclose()
                except Exception:
                    pass
            if not isinstance(items, list):
                continue
            for item in items:
                if item.get("uuid"):
                    result.append((api_url, item))
    return result


def _health_nodes_text(nodes: list[tuple[str, dict[str, Any]]], allowed: set[str]) -> str:
    if not nodes:
        return menu_text(
            "Ноды Remnawave",
            "Не удалось получить список нод. Проверьте, что панель доступна, "
            "а у токена есть права на чтение <code>/nodes</code>.",
        )
    total = len(nodes)
    selected = sum(1 for _, n in nodes if str(n.get("uuid")) in allowed)
    return menu_text(
        "Ноды для проверки",
        "Нажмите на строку, чтобы добавить ноду или убрать.",
        section("🩺 Ноды", f"Всего: {total}", f"Выбрано: {selected}"),
        quote(
            "Бот следит и гасит хосты только у отмеченных ✅ нод.",
            "Не отмечено ни одной — проверяются все. Ноды авто-балансировки просто не отмечайте, и бот их не тронет.",
        ),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "settings_remnawave"))
async def open_remnawave_settings(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        text=_root_text(),
        reply_markup=build_settings_remnawave_kb(_node_health_enabled(), _host_rotation_enabled()),
    )
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_node_menu"))
async def open_node_menu(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        text=_node_text(),
        reply_markup=build_settings_remnawave_node_kb(
            _node_health_enabled(), _node_interval(), _auto_disable_enabled()
        ),
    )
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_node_toggle"), flags={"popup": True})
async def toggle_node_health(callback: CallbackQuery) -> None:
    enabled = not _node_health_enabled()
    await _set_cfg(NODE_HEALTH_ENABLED=enabled)
    await callback.answer(
        "✅ Проверка включена" if enabled else "❌ Проверка выключена",
        show_alert=True,
    )
    await callback.message.edit_text(
        text=_node_text(),
        reply_markup=build_settings_remnawave_node_kb(
            _node_health_enabled(), _node_interval(), _auto_disable_enabled()
        ),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_node_interval"))
async def prompt_node_interval(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_text(
        text=(
            menu_text(
                "Интервал проверки нод",
                f"Сейчас: <b>{_node_interval()} мин.</b>",
                quote("Введите новое значение в минутах (1–1440). Частые опросы нагружают панель."),
            )
        ),
    )
    await state.set_state(RemnawaveSettingsState.waiting_for_node_interval)
    await callback.answer()


@router.message(RemnawaveSettingsState.waiting_for_node_interval)
async def set_node_interval(message: Message, state: FSMContext) -> None:
    try:
        value = int((message.text or "").strip())
    except ValueError:
        await message.answer(menu_text("Remnawave", "❌ Нужно число от 1 до 1440."))
        return
    if not 1 <= value <= 1440:
        await message.answer(menu_text("Remnawave", "❌ Диапазон: 1–1440 минут."))
        return
    await _set_cfg(NODE_HEALTH_INTERVAL_MIN=value)
    await state.clear()
    await message.answer(
        text=_node_text(),
        reply_markup=build_settings_remnawave_node_kb(
            _node_health_enabled(), _node_interval(), _auto_disable_enabled()
        ),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_autodisable_toggle"), flags={"popup": True})
async def toggle_auto_disable(callback: CallbackQuery) -> None:
    enabled = not _auto_disable_enabled()
    await _set_cfg(HOST_AUTO_DISABLE_ON_NODE_DOWN=enabled)
    await callback.answer(
        "✅ Авто-отключение хостов включено"
        if enabled
        else "❌ Авто-отключение хостов выключено",
        show_alert=True,
    )
    await callback.message.edit_text(
        text=_node_text(),
        reply_markup=build_settings_remnawave_node_kb(
            _node_health_enabled(), _node_interval(), _auto_disable_enabled()
        ),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_node_sync_now"))
async def run_host_sync_now(callback: CallbackQuery) -> None:
    from services.remnawave_monitor import sync_hosts_with_node_state

    await callback.answer(menu_text("Remnawave", "Синхронизирую…"))

    try:
        summary = await sync_hosts_with_node_state()
    except Exception as exc:
        logger.error("[Remnawave-Admin] Ошибка ручной синхронизации хостов: {}", exc)
        await callback.message.answer(
            menu_text("Синхронизация", "❌ Синхронизировать не удалось.", section("⚠️ Причина", str(exc)))
        )
        return

    blocks = [section("📊 Итог", f"Выключено: {len(summary['disabled'])}", f"Включено: {len(summary['enabled'])}")]
    if summary["disabled"]:
        blocks.append(section("⛔ Выключены", *summary["disabled"]))
    if summary["enabled"]:
        blocks.append(section("✅ Включены", *summary["enabled"]))
    if summary["errors"]:
        blocks.append(section("⚠️ Ошибки", *summary["errors"]))

    await callback.message.answer(menu_text("Синхронизация", card(*blocks)))


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_rot_menu"))
async def open_rotation_menu(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        text=_rotation_text(),
        reply_markup=build_settings_remnawave_rotation_kb(_host_rotation_enabled(), _rotation_interval()),
    )
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_rot_toggle"), flags={"popup": True})
async def toggle_rotation(callback: CallbackQuery) -> None:
    enabled = not _host_rotation_enabled()
    await _set_cfg(HOST_ROTATION_ENABLED=enabled)
    await callback.answer(
        "✅ Ротация включена" if enabled else "❌ Ротация выключена",
        show_alert=True,
    )
    await callback.message.edit_text(
        text=_rotation_text(),
        reply_markup=build_settings_remnawave_rotation_kb(_host_rotation_enabled(), _rotation_interval()),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_rot_interval"))
async def prompt_rotation_interval(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_text(
        text=menu_text(
            "Интервал ротации",
            f"Сейчас: <b>{_rotation_interval()} мин.</b>",
            quote("Введите новое значение в минутах (5–1440)."),
        ),
    )
    await state.set_state(RemnawaveSettingsState.waiting_for_rotation_interval)
    await callback.answer()


@router.message(RemnawaveSettingsState.waiting_for_rotation_interval)
async def set_rotation_interval(message: Message, state: FSMContext) -> None:
    try:
        value = int((message.text or "").strip())
    except ValueError:
        await message.answer(menu_text("Remnawave", "❌ Нужно число от 5 до 1440."))
        return
    if not 5 <= value <= 1440:
        await message.answer(menu_text("Remnawave", "❌ Допустимый диапазон: 5–1440 минут"))
        return
    await _set_cfg(HOST_ROTATION_INTERVAL_MIN=value)
    await state.clear()
    await message.answer(
        text=_rotation_text(),
        reply_markup=build_settings_remnawave_rotation_kb(_host_rotation_enabled(), _rotation_interval()),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "rw_rot_run_now"))
async def run_rotation_now(callback: CallbackQuery) -> None:
    from services.remnawave_monitor import run_host_rotation

    await callback.answer(menu_text("Remnawave", "Запускаю ротацию…"))

    try:
        summary = await run_host_rotation()
    except Exception as exc:
        logger.error("[Remnawave-Admin] Ошибка ручной ротации: {}", exc)
        await callback.message.answer(menu_text("Ротация", "❌ Ротация не удалась.", section("⚠️ Причина", str(exc))))
        return

    blocks = [
        section(
            "📊 Итог",
            f"Хостов: {summary['allowed_count']}",
            f"Панелей: {summary['panels']}",
            f"Переставлено: {summary['moved_total']}",
        )
    ]
    if summary["details"]:
        blocks.append(section("📋 Детали", *summary["details"]))
    if summary["errors"]:
        blocks.append(section("⚠️ Ошибки", *summary["errors"]))

    await callback.message.answer(menu_text("Ротация", card(*blocks)))


_HOSTS = SimpleNamespace(
    fetch=lambda: _fetch_from_panels("get_hosts", "хостов"),
    allowed=lambda: get_host_rotation_allowed(),
    key="HOST_ROTATION_ALLOWED",
    text=_hosts_text,
    kb=build_settings_remnawave_hosts_kb,
    loading="Загружаю хосты…",
    missing="Хост не найден",
    added="✅ Хост добавлен в ротацию",
    removed="▫️ Хост убран из ротации",
    page_on="✅ Включены",
)
_NODES = SimpleNamespace(
    fetch=lambda: _fetch_from_panels("get_all_nodes", "нод"),
    allowed=lambda: get_node_health_allowed(),
    key="NODE_HEALTH_ALLOWED",
    text=_health_nodes_text,
    kb=build_settings_remnawave_health_nodes_kb,
    loading="Загружаю ноды…",
    missing="Нода не найдена",
    added="✅ Нода добавлена в проверку",
    removed="▫️ Нода убрана из проверки",
    page_on="✅ Выбраны",
)
_PICKERS = {
    **dict.fromkeys(("rw_rot_hosts", "rw_rot_toggle_host", "rw_rot_select_all", "rw_rot_clear_page"), _HOSTS),
    **dict.fromkeys(("rw_node_sel", "rw_node_sel_toggle", "rw_node_sel_all", "rw_node_sel_clear"), _NODES),
}


async def _show_picker(callback: CallbackQuery, picker: SimpleNamespace, page: int, items: list, allowed: set[str]):
    await callback.message.edit_text(text=picker.text(items, allowed), reply_markup=picker.kb(page, items, allowed))


@router.callback_query(AdminPanelCallback.filter(F.action.in_(["rw_rot_hosts", "rw_node_sel"])))
async def open_picker(callback: CallbackQuery, callback_data: AdminPanelCallback) -> None:
    picker = _PICKERS[callback_data.action]
    await callback.answer(menu_text("Remnawave", picker.loading))
    items = await picker.fetch()
    await _show_picker(callback, picker, max(1, int(callback_data.page or 1)), items, picker.allowed())


@router.callback_query(
    AdminPanelCallback.filter(F.action.in_(["rw_rot_toggle_host", "rw_node_sel_toggle"])), flags={"popup": True}
)
async def toggle_picker_item(callback: CallbackQuery, callback_data: AdminPanelCallback) -> None:
    picker = _PICKERS[callback_data.action]
    idx = int(callback_data.page or 0)
    items = await picker.fetch()
    if idx < 0 or idx >= len(items):
        await callback.answer(picker.missing, show_alert=True)
        return
    item_uuid = str(items[idx][1].get("uuid"))
    allowed = picker.allowed()
    if item_uuid in allowed:
        allowed.discard(item_uuid)
        toast = menu_text("Remnawave", picker.removed)
    else:
        allowed.add(item_uuid)
        toast = menu_text("Remnawave", picker.added)
    await _set_cfg(**{picker.key: sorted(allowed)})
    await callback.answer(toast)
    await _show_picker(callback, picker, max(1, idx // REMNAWAVE_HOSTS_PER_PAGE + 1), items, allowed)


@router.callback_query(
    AdminPanelCallback.filter(
        F.action.in_(["rw_rot_select_all", "rw_rot_clear_page", "rw_node_sel_all", "rw_node_sel_clear"])
    )
)
async def set_picker_page(callback: CallbackQuery, callback_data: AdminPanelCallback) -> None:
    picker = _PICKERS[callback_data.action]
    select = callback_data.action in ("rw_rot_select_all", "rw_node_sel_all")
    items = await picker.fetch()
    allowed = picker.allowed()
    page = max(1, int(callback_data.page or 1))
    start = (page - 1) * REMNAWAVE_HOSTS_PER_PAGE
    for _, item in items[start : start + REMNAWAVE_HOSTS_PER_PAGE]:
        (allowed.add if select else allowed.discard)(str(item.get("uuid")))
    await _set_cfg(**{picker.key: sorted(allowed)})
    await callback.answer(menu_text("Remnawave", picker.page_on if select else "▫️ Сброшено"))
    await _show_picker(callback, picker, page, items, allowed)
