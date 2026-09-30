from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .runtime_sync import load_setting, register_runtime_config, update_setting


TARIFFS_CONFIG: dict[str, Any] = {
    "ALLOW_DOWNGRADE": True,
    "KEY_ADDONS_PACK_MODE": "all",
    "KEY_ADDONS_PRICE_BASE_MODE": "current",
    "KEY_ADDONS_RECALC_PRICE": False,
    "KEY_ADDONS_CARRY_ON_RENEWAL": False,
}
register_runtime_config("TARIFFS_CONFIG", TARIFFS_CONFIG)
_DESCRIPTION = "Конфигурация тарифов"


async def load_tariffs_config(session: AsyncSession) -> None:
    """Загружает конфиг тарифов из БД."""
    await load_setting(session, "TARIFFS_CONFIG", TARIFFS_CONFIG, TARIFFS_CONFIG, _DESCRIPTION)


async def update_tariffs_config(session: AsyncSession, new_values: dict[str, Any]) -> None:
    """Обновляет конфиг тарифов."""
    await update_setting(session, "TARIFFS_CONFIG", TARIFFS_CONFIG, new_values, TARIFFS_CONFIG, _DESCRIPTION)


def get_override_value(overrides: Any, key: int | str | None) -> Any:
    """Доплата за конкретный вариант. Ключ ищем и строкой, и числом:
    из JSONB приходят строки, а собранный в памяти тариф может нести int."""
    if not isinstance(overrides, dict) or key is None:
        return None
    if key in overrides:
        return overrides[key]
    text_key = str(key)
    if text_key in overrides:
        return overrides[text_key]
    try:
        int_key = int(text_key)
    except (TypeError, ValueError):
        return None
    return overrides.get(int_key)


def normalize_tariff_config(tariff: dict[str, Any]) -> dict[str, Any]:
    raw_duration_options = tariff.get("duration_options") or []
    duration_options: list[int] = []
    for value in raw_duration_options:
        try:
            v = int(value)
        except (TypeError, ValueError):
            continue
        if v > 0:
            duration_options.append(v)
    if not duration_options:
        base_duration = int(tariff.get("duration_days") or 0) or 30
        duration_options = [base_duration]
    duration_options = sorted(set(duration_options))

    raw_device_options = tariff.get("device_options") or []
    device_options: list[int] = []
    for value in raw_device_options:
        try:
            v = int(value)
        except (TypeError, ValueError):
            continue
        if v > 0:
            device_options.append(v)
    if not device_options:
        base_device_limit = int(tariff.get("device_limit") or 0)
        if base_device_limit > 0:
            device_options = [base_device_limit]
        else:
            device_options = []
    device_options = sorted(set(device_options))

    raw_traffic_options = tariff.get("traffic_options_gb")
    traffic_options_gb: list[int] | None
    if raw_traffic_options is None:
        traffic_options_gb = None
    else:
        traffic_values: list[int] = []
        has_unlimited = False
        for value in raw_traffic_options:
            try:
                v = int(value)
            except (TypeError, ValueError):
                continue
            if v == 0:
                has_unlimited = True
            elif v > 0:
                traffic_values.append(v)
        if not traffic_values and not has_unlimited:
            traffic_options_gb = None
        else:
            unique_values = sorted(set(traffic_values))
            if has_unlimited:
                traffic_options_gb = [0] + unique_values
            else:
                traffic_options_gb = unique_values

    return {
        "duration_options": duration_options,
        "device_options": device_options,
        "traffic_options_gb": traffic_options_gb,
    }
