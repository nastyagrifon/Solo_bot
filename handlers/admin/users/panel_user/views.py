"""Тексты и клавиатуры: секция пользователя панели в карточке клиента и экраны правки.

Только сборка, без запросов.
"""

import html

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from settings.buttons import BACK

from ...panel.headers import card, menu_text, section
from ..keyboard import AdminUserEditorCallback, LegacyTgRefAlias


# Длинная заметка раздула бы карточку клиента за предел сообщения Telegram.
NOTE_PREVIEW = 200

# Поля, которые правятся вводом текста: заголовок, подсказка, имя поля в панели.
FIELD_PROMPTS = {
    "tag": ("Тег", "Пришлите тег: латинские буквы, цифры и «_», до 16 символов.\n«-» очищает поле.", "tag"),
    "note": ("Заметка", "Пришлите текст заметки.\n«-» очищает поле.", "description"),
}
TRANSFER_TITLE = "Перенос подписки"


class PanelUserCallback(LegacyTgRefAlias, CallbackData, prefix="kpu"):
    """Кнопки экранов правки. Uuid сквадов сюда не влезают: передаётся номер в списке из FSM."""

    action: str
    user_id: int
    key_ref: str
    field: str = ""
    idx: int = -1


def _cb(base: PanelUserCallback, action: str, **values) -> str:
    return PanelUserCallback(action=action, user_id=base.user_id, key_ref=base.key_ref, **values).pack()


def _back(base: PanelUserCallback) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=BACK, callback_data=_cb(base, "back"))


def _value(value) -> str:
    return html.escape(str(value)) if value not in (None, "") else "—"


def _title(email: str) -> str:
    # У клиента бывает несколько подписок — заголовок говорит, чья секция.
    return f"🧩 Панель · {_value(email)}"


def panel_section(user: dict, external_squads: list[dict], email: str) -> str:
    """Секция карточки клиента: поля пользователя панели за выбранной подпиской."""
    ext_uuid = user.get("externalSquadUuid")
    ext_name = next((s.get("name") for s in external_squads if s.get("uuid") == ext_uuid), ext_uuid)
    internal = [
        s.get("name") or s.get("uuid") if isinstance(s, dict) else s for s in user.get("activeInternalSquads") or []
    ]
    note = user.get("description") or ""
    if len(note) > NOTE_PREVIEW:
        note = note[:NOTE_PREVIEW] + "…"
    return section(
        _title(email),
        f"Внешний сквад: {_value(ext_name)}",
        f"Внутренние: {_value(', '.join(map(str, internal)))}",
        f"TG ID: {_value(user.get('telegramId'))}",
        f"Тег: {_value(user.get('tag'))}",
        f"Заметка: {_value(note)}",
    )


def panel_section_failed(email: str) -> str:
    return section(_title(email), "Панель не ответила")


def client_panel_rows(user_id: int, selected: str, subs: list[tuple[str, str]]) -> list[list[InlineKeyboardButton]]:
    """Ряды карточки клиента: выбор подписки (если их больше одной) и правка выбранной.

    subs — пары [ссылка подписки, почта] в порядке карточки.
    """

    def button(label: str, action: str, ref: str = selected) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=label, callback_data=AdminUserEditorCallback(action=action, user_id=user_id, data=ref).pack()
        )

    rows = []
    if len(subs) > 1:
        refs = [ref for ref, _ in subs]
        pos = refs.index(selected)
        rows.append([
            button("◀", "users_kpu_pick", refs[pos - 1]),
            button(subs[pos][1], "users_kpu_pick"),
            button("▶", "users_kpu_pick", refs[(pos + 1) % len(refs)]),
        ])
    rows += [
        [button("👥 Внешний сквад", "users_kpu_ext"), button("🧩 Внутренние", "users_kpu_int")],
        [button("🏷 Тег", "users_kpu_tag"), button("📝 Заметка", "users_kpu_note")],
        [button("🆔 Сменить владельца", "users_kpu_tg")],
    ]
    return rows


def external_text() -> str:
    return menu_text("Внешний сквад", "Выберите один сквад или уберите текущий.")


def external_kb(base: PanelUserCallback, squads: list[list[str]], current: str | None) -> InlineKeyboardMarkup:
    """squads — пары [uuid, имя] в том порядке, в каком лежат в FSM."""
    builder = InlineKeyboardBuilder()
    for idx, (uuid, name) in enumerate(squads):
        mark = "✅ " if uuid == current else ""
        builder.row(InlineKeyboardButton(text=f"{mark}{name}", callback_data=_cb(base, "ext_set", idx=idx)))
    builder.row(InlineKeyboardButton(text="🚫 Без сквада", callback_data=_cb(base, "ext_set", idx=-1)))
    builder.row(_back(base))
    return builder.as_markup()


def internal_text() -> str:
    return menu_text("Внутренние сквады", "Отметьте нужные и нажмите «Сохранить».")


def internal_kb(base: PanelUserCallback, squads: list[list[str]], selected: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for idx, (uuid, name) in enumerate(squads):
        mark = "✅" if uuid in selected else "▫️"
        builder.row(InlineKeyboardButton(text=f"{mark} {name}", callback_data=_cb(base, "int_tg", idx=idx)))
    builder.row(InlineKeyboardButton(text="💾 Сохранить", callback_data=_cb(base, "int_save")))
    builder.row(_back(base))
    return builder.as_markup()


def ask_text(field: str, user: dict) -> str:
    title, hint, panel_field = FIELD_PROMPTS[field]
    return menu_text(title, card(section(title, f"Сейчас: {_value(user.get(panel_field))}")), hint)


def ask_kb(base: PanelUserCallback, field: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🗑 Очистить", callback_data=_cb(base, "clr", field=field)))
    builder.row(_back(base))
    return builder.as_markup()


def transfer_ask_text(email: str, owner: str) -> str:
    return menu_text(
        TRANSFER_TITLE,
        card(section("Подписка", f"Почта: {_value(email)}", f"Владелец: {_value(owner)}")),
        "Пришлите TG ID клиента, которому передать подписку. Клиент уже должен быть в боте.",
    )


def transfer_confirm_text(email: str, owner: str, target: str) -> str:
    return menu_text(
        TRANSFER_TITLE,
        f"Перенести подписку <b>{_value(email)}</b> от {_value(owner)} к {_value(target)}?",
        "Срок, тариф и история подписки уйдут новому владельцу. "
        "Платежи останутся у прежнего, автоплатёж подписки отключится.",
    )


def transfer_confirm_kb(base: PanelUserCallback) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Перенести", callback_data=_cb(base, "mv_ok")))
    builder.row(_back(base))
    return builder.as_markup()


def back_kb(base: PanelUserCallback) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_back(base)]])
