"""Блок HWID на прослойке подписки: заблокированное устройство не получает подписку.

Протокол ручки: ``POST`` JSON на ``HWID_BLOCK_API_URL`` и три заголовка —
``X-Bot-Ts`` (unix-время), ``X-Bot-Nonce`` (32 hex, новый на каждый запрос) и
``X-Bot-Sig`` = hex HMAC-SHA256 с ключом ``HWID_BLOCK_API_SECRET`` над
``ts + "\\n" + nonce + "\\n" + тело``. Неверную подпись ручка не отличает от
несуществующего адреса. Операции: ``hwid_block``, ``hwid_unblock``,
``hwid_status``; ``by: admin`` ставит блок с источником ``bot_admin``.

Адрес и секрет необязательны: без них `configured()` ложно и экран устройств
работает без блокировок. Секрет не попадает ни в журнал, ни в тексты ошибок.
"""

import hashlib
import hmac
import json
import secrets
import time


TIMEOUT_SEC = 8
# Статус спрашивается на каждом показе экрана: лежащая прослойка не должна его вешать.
STATUS_TIMEOUT_SEC = 3
STATUS_BATCH = 100
ADMIN_SOURCE = "bot_admin"
CLIENT_SOURCE = "bot"


class HwidBlockError(Exception):
    """Прослойка не настроена, не ответила или отказала."""


def _settings() -> tuple[str, str]:
    from settings import config

    return (
        str(getattr(config, "HWID_BLOCK_API_URL", "") or ""),
        str(getattr(config, "HWID_BLOCK_API_SECRET", "") or ""),
    )


def configured() -> bool:
    url, secret = _settings()
    return bool(url and secret)


def sign(secret: str, ts: str, nonce: str, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{ts}\n{nonce}\n".encode() + body, hashlib.sha256).hexdigest()


async def call(op: str, timeout: float = TIMEOUT_SEC, **fields) -> dict:
    """Один запрос к ручке; любая неудача — `HwidBlockError`."""
    url, secret = _settings()
    if not (url and secret):
        raise HwidBlockError("прослойка не настроена")
    body = json.dumps({"op": op, **fields}, ensure_ascii=False).encode()
    ts, nonce = str(int(time.time())), secrets.token_hex(16)
    headers = {
        "Content-Type": "application/json",
        "X-Bot-Ts": ts,
        "X-Bot-Nonce": nonce,
        "X-Bot-Sig": sign(secret, ts, nonce, body),
    }
    import aiohttp

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as http:
            async with http.post(url, data=body, headers=headers) as resp:
                status, raw = resp.status, await resp.text()
    except Exception as error:
        raise HwidBlockError(f"{op}: сеть, {type(error).__name__}") from error
    try:
        answer = json.loads(raw)
    except ValueError:
        answer = None
    if status != 200 or not isinstance(answer, dict) or not answer.get("ok"):
        reason = answer.get("error") if isinstance(answer, dict) else None
        raise HwidBlockError(f"{op}: HTTP {status}" + (f", {reason}" if reason else ""))
    return answer


async def block(hwid: str, username: str = "", note: str = "") -> dict:
    return await call("hwid_block", hwid=hwid, username=username, note=note, by="admin")


async def unblock(hwid: str) -> dict:
    """Снимает блоки бота (свой и клиента); ручной остаётся: ``blocked: true``."""
    return await call("hwid_unblock", hwid=hwid, by="admin")


async def status(hwids: list[str]) -> dict[str, str]:
    """``{hwid: источник}`` для заблокированных из списка."""
    blocked: dict[str, str] = {}
    for start in range(0, len(hwids), STATUS_BATCH):
        answer = await call("hwid_status", timeout=STATUS_TIMEOUT_SEC, hwids=hwids[start : start + STATUS_BATCH])
        part = answer.get("blocked")
        if isinstance(part, dict):  # пустой набор ручка отдаёт списком
            blocked.update(part)
    return blocked


async def status_or_none(hwids: list[str]) -> dict[str, str] | None:
    """Статус для экрана: None — блокировки показать нельзя, экран рисуется без них."""
    if not configured():
        return None
    try:
        return await status(hwids)
    except HwidBlockError as error:
        from logger import logger

        logger.warning(f"[hwid_block] статус блокировок не получен: {error}")
        return None
