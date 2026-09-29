"""Подпись и форма конверта события панели. Без aiohttp и базы — проверяется голым python."""

import hashlib
import hmac


def signature_valid(raw_body: bytes, signature_header: str | None, secret: str) -> bool:
    """HMAC-SHA256 тела запроса секретом панели.

    Принимаем голый hex и форму ``sha256=<hex>``: панель писала так в разных
    версиях. Пустой секрет здесь — всегда отказ: решение «проверять или нет»
    принимается выше, при регистрации маршрута.
    """
    if not secret or not signature_header:
        return False
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    candidate = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(candidate, digest)


def _sources(payload: dict):
    """Корень конверта и его ``data``: события кладут поля то туда, то туда."""
    yield payload
    data = payload.get("data")
    if isinstance(data, dict):
        yield data


def extract_event(payload: dict) -> str | None:
    """Имя события: ``event``, в части релизов ``type`` или ``name``."""
    for source in _sources(payload):
        for key in ("event", "type", "name"):
            value = source.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def extract_data(payload: dict) -> dict:
    """Полезная часть конверта. Нет ``data`` — событие плоское."""
    data = payload.get("data")
    return data if isinstance(data, dict) else payload
