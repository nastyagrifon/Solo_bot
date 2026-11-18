"""Экран «Сквады тестировщиков» в управлении админами: выбор сквадов и применение.

Право — то же, что на управление админами: роль tester выдаёт этот же экран,
и кто вправе назначить тестировщика, тот решает, что ему ставится в панели.
Запросы к панели и сохранение — в users.panel_user.testers.
"""

import html

from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from filters.admin import HasPermission
from filters.permissions import PERM_ADMINS
from settings.buttons import BACK

from ..panel.headers import menu_text, section
from ..panel.keyboard import AdminPanelCallback
from ..users.panel_user import testers
from ..users.panel_user.service import PanelUserError, toggle
from . import router


TITLE = "Сквады тестировщиков"
POPUP = {"popup": True}
# Списки сквадов панели в FSM: в кнопку uuid не влезает, передаётся номер.
EXT, INT, SEL = "tst_ext", "tst_int", "tst_int_sel"


def _cb(action: str) -> str:
    return AdminPanelCallback(action=action).pack()


def _btn(text: str, action: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=text, callback_data=_cb(action))]


def _names(pairs: list[list[str]], uuids: list[str]) -> str:
    named = dict(pairs)
    return ", ".join(html.escape(named.get(u, u)) for u in uuids) or "—"


def _pairs(squads: list[dict]) -> list[list[str]]:
    return [[str(s["uuid"]), str(s.get("name") or s["uuid"])] for s in squads if s.get("uuid")]


def main_text(data: dict) -> str:
    has_external, external, internal = testers.configured()
    ext = "не задан" if not has_external else (_names(data.get(EXT) or [], [external]) if external else "без сквада")
    return menu_text(
        TITLE,
        section("Настройка", f"Внешний: {ext}", f"Внутренние: {_names(data.get(INT) or [], internal)}",
                f"Тег: {testers.TESTER_TAG}"),
        "Ставится на подписки Remnawave админов с ролью tester: кнопкой ниже и при назначении роли. "
        "Чужой тег не перезаписывается, снятие роли убирает только TESTER.",
    )


def main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        _btn("👥 Внешний сквад", "tst_ext"),
        _btn("🧩 Внутренние", "tst_int"),
        _btn("▶️ Применить тестировщикам", "tst_apply"),
        _btn(BACK, "admins"),
    ])


async def _edit(callback: CallbackQuery, text: str, kb) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


async def _picked(state: FSMContext, name: str, idx: int) -> str:
    pairs = (await state.get_data()).get(name) or []
    if not 0 <= idx < len(pairs):
        raise PanelUserError("Список устарел, откройте его заново.")
    return pairs[idx][0]


@router.callback_query(AdminPanelCallback.filter(F.action == "tst"), HasPermission(PERM_ADMINS), flags=POPUP)
async def show_testers(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    try:
        external, internal = await testers.panel_squads(session)
    except PanelUserError as error:
        await callback.answer(f"❌ {error}", show_alert=True)
        return
    await state.update_data({EXT: _pairs(external), INT: _pairs(internal), SEL: testers.configured()[2]})
    await callback.answer()
    await _edit(callback, main_text(await state.get_data()), main_kb())


@router.callback_query(AdminPanelCallback.filter(F.action == "tst_ext"), HasPermission(PERM_ADMINS))
async def show_external(callback: CallbackQuery, state: FSMContext):
    current = testers.configured()[1]
    rows = [
        _btn(f"{'✅ ' if uuid == current else ''}{name}", f"tst_ext_set|{idx}")
        for idx, (uuid, name) in enumerate((await state.get_data()).get(EXT) or [])
    ]
    rows += [_btn("🚫 Без сквада", "tst_ext_set|-1"), _btn(BACK, "tst")]
    await _edit(callback, menu_text(TITLE, "Один внешний сквад для всех тестировщиков."),
                InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(
    AdminPanelCallback.filter(F.action.startswith("tst_ext_set|")), HasPermission(PERM_ADMINS), flags=POPUP
)
async def set_external(callback: CallbackQuery, callback_data: AdminPanelCallback, session: AsyncSession,
                       state: FSMContext):
    idx = int(callback_data.action.split("|")[1])
    try:
        squad = None if idx == -1 else await _picked(state, EXT, idx)
    except PanelUserError as error:
        await callback.answer(f"❌ {error}", show_alert=True)
        return
    await testers.save_external(session, squad)
    await callback.answer("✅ Сохранено")
    await _edit(callback, main_text(await state.get_data()), main_kb())


def _internal_kb(pairs: list[list[str]], selected: list[str]) -> InlineKeyboardMarkup:
    rows = [
        _btn(f"{'✅' if uuid in selected else '▫️'} {name}", f"tst_int_tg|{idx}")
        for idx, (uuid, name) in enumerate(pairs)
    ]
    rows += [_btn("💾 Сохранить", "tst_int_save"), _btn(BACK, "tst")]
    return InlineKeyboardMarkup(inline_keyboard=rows)


INTERNAL_TEXT = menu_text(TITLE, "Внутренние сквады тестировщиков. Отметьте и нажмите «Сохранить».")


@router.callback_query(AdminPanelCallback.filter(F.action == "tst_int"), HasPermission(PERM_ADMINS))
async def show_internal(callback: CallbackQuery, state: FSMContext):
    selected = testers.configured()[2]
    await state.update_data({SEL: selected})
    await _edit(callback, INTERNAL_TEXT, _internal_kb((await state.get_data()).get(INT) or [], selected))


@router.callback_query(
    AdminPanelCallback.filter(F.action.startswith("tst_int_tg|")), HasPermission(PERM_ADMINS), flags=POPUP
)
async def toggle_internal(callback: CallbackQuery, callback_data: AdminPanelCallback, state: FSMContext):
    try:
        uuid = await _picked(state, INT, int(callback_data.action.split("|")[1]))
    except PanelUserError as error:
        await callback.answer(f"❌ {error}", show_alert=True)
        return
    data = await state.get_data()
    selected = toggle(data.get(SEL) or [], uuid)
    await state.update_data({SEL: selected})
    await callback.answer()
    await _edit(callback, INTERNAL_TEXT, _internal_kb(data.get(INT) or [], selected))


@router.callback_query(AdminPanelCallback.filter(F.action == "tst_int_save"), HasPermission(PERM_ADMINS), flags=POPUP)
async def save_internal(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    if SEL not in data:
        await callback.answer("❌ Список устарел, откройте его заново.", show_alert=True)
        return
    await testers.save_internal(session, data[SEL])
    await callback.answer("✅ Сохранено")
    await _edit(callback, main_text(await state.get_data()), main_kb())


@router.callback_query(AdminPanelCallback.filter(F.action == "tst_apply"), HasPermission(PERM_ADMINS), flags=POPUP)
async def apply_testers(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    done, failed, kept = await testers.apply_all(session)
    await callback.answer("Готово" if not failed else "Есть сбои", show_alert=bool(failed))
    await _edit(callback, menu_text(TITLE, testers.report(done, failed, kept)), main_kb())
