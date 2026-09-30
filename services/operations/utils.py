from sqlalchemy.ext.asyncio import AsyncSession

from database import get_servers


def split_by_panel(servers: list) -> tuple[list, list]:
    xui = []
    remna = []
    for s in servers:
        pt = str(s.get("panel_type", "3x-ui")).lower()
        if pt == "3x-ui":
            xui.append(s)
        elif pt == "remnawave":
            remna.append(s)
    return xui, remna


def bytes_from_gb(total_gb: int) -> int:
    return total_gb * 1024 * 1024 * 1024 if total_gb else 0


def is_plan_vless(plan) -> bool:
    if plan is None:
        return False
    if isinstance(plan, dict):
        return bool(plan.get("vless"))
    return bool(getattr(plan, "vless", False))


def norm_name(x: str | None) -> str:
    return (x or "").strip().lower()


def unique_by_api_url(servers: list) -> list:
    seen = set()
    out = []
    for s in servers or []:
        url = (s.get("api_url") or "").rstrip("/")
        if url and url not in seen:
            seen.add(url)
            out.append(s)
    return out


async def resolve_cluster(session: AsyncSession, cluster_id: str):
    """Возвращает список серверов для кластера или конкретного сервера."""
    servers = await get_servers(session)
    cluster = servers.get(cluster_id)
    if cluster:
        return cluster
    found = []
    for _key, server_list in servers.items():
        for s in server_list:
            if s.get("server_name", "").lower() == cluster_id.lower():
                found.append(s)
    if found:
        return found
    raise ValueError(f"Кластер или сервер с ID/именем {cluster_id} не найден.")
