"""Кто владеет возможностью: ядро или модуль.

Ядро умеет вести обращения само — заводит топик на обращение, постит в форум,
забирает ответы агентов. Модуль поддержки умеет то же, но по-своему: топик на
КЛИЕНТА, автосообщения, ночной режим, карточки. Включить оба значит вести
одного человека в двух местах.

Прежде ядро спрашивало хук и смотрело, вернул ли тот `True`. Так владение
определялось на каждом событии, и молчащий обработчик было не отличить от
отсутствующего. Теперь модуль объявляет намерение ОДИН раз при загрузке, а
ядро только смотрит, занято ли оно.

Намерение заявляет тот, кто готов довести дело до конца. Не заявил — ядро
делает по-своему, то есть выключенный или упавший модуль ничего не ломает.
"""
from __future__ import annotations


class _Quiet:
    """Заглушка журнала: проверки грузят этот файл без установленного бота."""

    def info(self, *_a, **_k):
        return None

    warning = error = info


def _log():
    """Логгер ядра, если он есть. Реестр владения не повод падать из-за него."""
    try:
        from logger import logger

        return logger
    except Exception:                                            # noqa: BLE001
        return _Quiet()


#: Доставка обращений из кабинета операторам: топик, сообщения, вложения.
TICKETS_DELIVERY = "tickets.delivery"

ALL_INTENTS: tuple[str, ...] = (TICKETS_DELIVERY,)

_owners: dict[str, str] = {}


def claim(intent: str, owner: str) -> bool:
    """Объявить намерение. False — его уже занял другой, владелец не сменился.

    Первый победил намеренно: два модуля с одним намерением — это ошибка
    настройки, и менять владельца на ходу значило бы доставлять обращения
    то одним путём, то другим.
    """
    if intent not in ALL_INTENTS:
        _log().warning(f"[Intents] неизвестное намерение: {intent}")
        return False
    current = _owners.get(intent)
    if current and current != owner:
        _log().warning(
            f"[Intents] «{intent}» уже у «{current}», заявка «{owner}» отклонена"
        )
        return False
    _owners[intent] = owner
    _log().info(f"[Intents] «{intent}» ведёт «{owner}»")
    return True


def owner_of(intent: str) -> str | None:
    """Кто ведёт намерение. None — ядро делает по-своему."""
    return _owners.get(intent)


def taken(intent: str) -> bool:
    """Занято ли намерение кем-то, кроме ядра."""
    return bool(_owners.get(intent))


def release(intent: str, owner: str) -> bool:
    """Отказаться от намерения. Чужое не отпускаем."""
    if _owners.get(intent) != owner:
        return False
    _owners.pop(intent, None)
    _log().info(f"[Intents] «{intent}» свободно, «{owner}» отказался")
    return True
