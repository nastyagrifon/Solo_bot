"""Тексты и клавиатуры экранов пользователя панели. Только сборка, без запросов."""

import html

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from settings.buttons import BACK

from ...panel.headers import card, menu_text, quote, section
from ..keyboard import AdminUserEditorCallback, LegacyTgRefAlias


TITLE = "Пользователь панели"

# Поля, которые правятся вводом текста: заголовок, подсказка, имя поля в панели.
FIELD_PROMPTS = {
    "tg": ("TG ID", "Пришлите Telegram ID числом.\n«-» очищает поле.", "telegramId"),
    "tag": ("Тег", "Пришлите тег: латинские буквы, цифры и «_», до 16 символов.\n«-» очищает поле.", "tag"),
    "note": ("Заметка", "Пришлите текст заметки.\n«-» очищает поле.", "description"),
}


class PanelUserCallback(LegacyTgRefAlias, CallbackData, prefix="kpu"):
    """Кнопки экранов. Uuid сквадов сюда не влезают: передаётся номер в списке из FSM."""

    action: str
    user_id: int
    key_ref: str
    field: str = ""
    idx: int = -1


def _cb(base: PanelUserCallback, action: str, **values) -> str:
    return PanelUserCallback(action=action, user_id=base.user_id, key_ref=base.key_ref, **values).pack()


def _back_to_card(base: PanelUserCallback) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=BACK, callback_data=_cb(base, "card"))


def _value(value) -> str:
    return html.escape(str(value)) if value not in (None, "") else "—"


def card_text(user: dict, owner_tg_id: int | None, external_squads: list[dict]) -> str:
    ext_uuid = user.get("externalSquadUuid")
    ext_name = next((s.get("name") for s in external_squads if s.get("uuid") == ext_uuid), ext_uuid)
    internal = [
        s.get("name") or s.get("uuid") if isinstance(s, dict) else s for s in user.get("activeInternalSquads") or []
    ]
    blocks = [
        section("🧩 Панель", f"Имя: {_value(user.get('username'))}", f"Статус: {_value(user.get('status'))}"),
        section(
            "👥 Сквады",
            f"Внешний: {_value(ext_name)}",
            f"Внутренние: {_value(', '.join(map(str, internal)))}",
        ),
        section(
            "🆔 Telegram",
            f"В панели: {_value(user.get('telegramId'))}",
            f"Владелец ключа: {_value(owner_tg_id)}",
        ),
        section("🏷 Тег", f"Тег: {_value(user.get('tag'))}"),
    ]
    description = user.get("description")
    blocks.append(quote(f"📝 {html.escape(description)}") if description else section("📝 Заметка", "Заметка: —"))
    return menu_text(TITLE, card(*blocks))


def card_kb(base: PanelUserCallback) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="👥 Внешний сквад", callback_data=_cb(base, "ext")),
        InlineKeyboardButton(text="🧩 Внутренние", callback_data=_cb(base, "int")),
    )
    builder.row(
        InlineKeyboardButton(text="🆔 TG ID", callback_data=_cb(base, "ask", field="tg")),
        InlineKeyboardButton(text="🏷 Тег", callback_data=_cb(base, "ask", field="tag")),
    )
    builder.row(
        InlineKeyboardButton(text="📝 Заметка", callback_data=_cb(base, "ask", field="note")),
        InlineKeyboardButton(
            text="💻 Устройства",
            callback_data=AdminUserEditorCallback(
                action="users_hwid_menu", data=base.key_ref, user_id=base.user_id
            ).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminUserEditorCallback(
                action="users_key_edit", data=base.key_ref, user_id=base.user_id
            ).pack(),
        )
    )
    return builder.as_markup()


def external_text() -> str:
    return menu_text("Внешний сквад", "Выберите один сквад или уберите текущий.")


def external_kb(base: PanelUserCallback, squads: list[list[str]], current: str | None) -> InlineKeyboardMarkup:
    """squads — пары [uuid, имя] в том порядке, в каком лежат в FSM."""
    builder = InlineKeyboardBuilder()
    for idx, (uuid, name) in enumerate(squads):
        mark = "✅ " if uuid == current else ""
        builder.row(InlineKeyboardButton(text=f"{mark}{name}", callback_data=_cb(base, "ext_set", idx=idx)))
    builder.row(InlineKeyboardButton(text="🚫 Без сквада", callback_data=_cb(base, "ext_set", idx=-1)))
    builder.row(_back_to_card(base))
    return builder.as_markup()


def internal_text() -> str:
    return menu_text("Внутренние сквады", "Отметьте нужные и нажмите «Сохранить».")


def internal_kb(base: PanelUserCallback, squads: list[list[str]], selected: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for idx, (uuid, name) in enumerate(squads):
        mark = "✅" if uuid in selected else "▫️"
        builder.row(InlineKeyboardButton(text=f"{mark} {name}", callback_data=_cb(base, "int_tg", idx=idx)))
    builder.row(InlineKeyboardButton(text="💾 Сохранить", callback_data=_cb(base, "int_save")))
    builder.row(_back_to_card(base))
    return builder.as_markup()


def ask_text(field: str, user: dict, owner_tg_id: int | None) -> str:
    title, hint, panel_field = FIELD_PROMPTS[field]
    lines = [f"Сейчас: {_value(user.get(panel_field))}"]
    if field == "tg":
        lines.append(f"Владелец ключа: {_value(owner_tg_id)}")
    return menu_text(title, card(section(title, *lines)), hint)


def ask_kb(base: PanelUserCallback, field: str, owner_tg_id: int | None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if field == "tg" and owner_tg_id:
        label = f"👤 Как у владельца ключа ({owner_tg_id})"
        builder.row(InlineKeyboardButton(text=label, callback_data=_cb(base, "own")))
    builder.row(InlineKeyboardButton(text="🗑 Очистить", callback_data=_cb(base, "clr", field=field)))
    builder.row(_back_to_card(base))
    return builder.as_markup()
