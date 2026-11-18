"""Разбор ввода админа для полей юзера панели.

Чистые функции: без сети и базы. Ошибку возвращают текстом для админа,
чтобы хендлер показал её как есть, а не отправил в панель заведомо
отклоняемое значение.
"""

import re

CLEAR = "-"
TAG_RE = re.compile(r"^[A-Z0-9_]{1,16}$")
DESCRIPTION_MAX = 1000


class InputError(ValueError):
    """Ввод не прошёл проверку; текст — для админа."""


def parse_tag(raw: str | None) -> str | None:
    """Тег панели: A-Z, 0-9, _, до 16 символов. «-» — убрать тег."""
    value = (raw or "").strip()
    if value == CLEAR:
        return None
    value = value.upper()
    if not TAG_RE.fullmatch(value):
        raise InputError("Тег: только латиница A-Z, цифры и _, до 16 символов.")
    return value


def parse_telegram_id(raw: str | None) -> int:
    """TG ID клиента-получателя подписки: положительное число, очистки нет."""
    value = (raw or "").strip()
    # isdigit() пускает «²» и арабские цифры, а Telegram ID не длиннее 15 знаков.
    if not (value.isascii() and value.isdigit()) or len(value) > 15 or int(value) <= 0:
        raise InputError("TG ID — положительное число.")
    return int(value)


def parse_description(raw: str | None) -> str | None:
    """Заметка панели. «-» или пусто — убрать заметку."""
    value = (raw or "").strip()
    if value in ("", CLEAR):
        return None
    if len(value) > DESCRIPTION_MAX:
        raise InputError(f"Заметка длиннее {DESCRIPTION_MAX} символов.")
    return value
