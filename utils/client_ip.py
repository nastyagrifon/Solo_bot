"""Адрес клиента за обратным прокси: один разбор X-Forwarded-For на весь проект.

Заголовок присылает кто угодно, поэтому доверять ему можно ровно настолько,
насколько доверяешь звену, от которого пришёл запрос. Раньше адрес разбирался в
двух местах по-разному: `api/v2/routes/auth/_common._client_ip` отбрасывал
заголовок целиком, если peer не петля (а за докер-мостом это всегда так), а
`database/identities._request_meta` брал первый элемент вообще без проверок —
то есть в историю входов и в ключ rate-limit попадало то, что клиент сам
написал в заголовке.
"""

import ipaddress
import os


# Прокси у нас всегда рядом: Caddy на петле либо контейнер в докер-сети.
# Переопределить список можно переменной окружения TRUSTED_PROXY_CIDRS
# (через запятую) — например когда бот стоит за внешним балансировщиком.
DEFAULT_TRUSTED_PROXY_CIDRS = (
    "127.0.0.0/8",
    "::1/128",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "fc00::/7",
)


def _load_trusted_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    raw = os.getenv("TRUSTED_PROXY_CIDRS", "").strip()
    sources = [p.strip() for p in raw.split(",") if p.strip()] if raw else list(DEFAULT_TRUSTED_PROXY_CIDRS)
    networks = []
    for item in sources:
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue
    return tuple(networks)


TRUSTED_PROXY_NETWORKS = _load_trusted_networks()


def _parse_ip(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Разбирает один элемент цепочки: адрес в скобках, адрес с портом, мусор → None."""
    value = (raw or "").strip()
    if not value:
        return None
    if value.startswith("["):
        value = value[1:].split("]", 1)[0]
    elif value.count(":") == 1 and "." in value:
        value = value.split(":", 1)[0]
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _is_trusted(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(ip in network for network in TRUSTED_PROXY_NETWORKS)


def client_ip(request) -> str:
    """Адрес клиента: идёт по цепочке от себя наружу, пока звенья доверенные.

    Первое недоверенное звено и есть клиент: подделанные элементы заголовка
    остаются левее него и до результата не доходят. Прямое подключение (peer не
    в доверенных сетях) заголовок не читает вовсе.

    Если вся цепочка оказалась внутри доверенных сетей, спрашиваем X-Real-IP:
    промежуточный слой (SSR веб-кабинета) умеет перезаписывать X-Forwarded-For
    своим адресом, а этот заголовок пробрасывает нетронутым. Доверять ему можно
    по той же причине, что и цепочке: запрос пришёл от доверенного звена, а
    фронт-прокси значение заголовка ЗАМЕНЯЕТ, а не дополняет.
    """
    if request is None:
        return ""
    try:
        peer_raw = (request.client.host if request.client else "") or ""
    except Exception:
        peer_raw = ""
    try:
        forwarded = request.headers.get("x-forwarded-for") or ""
    except Exception:
        forwarded = ""

    hops = [h for h in forwarded.split(",")] if forwarded else []
    # Ближайшее к нам звено — peer, дальше заголовок читается справа налево.
    chain = [peer_raw] + list(reversed(hops))

    result = ""
    for hop in chain:
        ip = _parse_ip(hop)
        if ip is None:
            # Мусор в цепочке: дальше ей верить нельзя, отдаём последнее честное.
            break
        result = str(ip)
        if not _is_trusted(ip):
            return result

    # Сюда попадаем, когда клиента в цепочке нет: либо заголовка нет вовсе, либо
    # все его звенья наши. Тогда адрес может лежать в X-Real-IP.
    try:
        real_raw = request.headers.get("x-real-ip") or ""
    except Exception:
        real_raw = ""
    real = _parse_ip(real_raw)
    if real is not None and not _is_trusted(real):
        return str(real)
    return result or peer_raw.strip()
