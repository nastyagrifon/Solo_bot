import importlib
import pkgutil

from pathlib import Path

from aiogram import Router

from logger import logger

from .modules_manager import _register_legacy_webhook, manager


modules_hub = Router(name="modules_hub")


def _is_safe_module_name(name: str) -> bool:
    return bool(name and name.isidentifier() and "." not in name and "/" not in name and "\\" not in name)


def load_modules_from_folder(folder: str = "modules") -> list[Router]:
    routers = []
    base_path = Path(folder)

    for _finder, name, _ispkg in pkgutil.iter_modules([str(base_path)]):
        name = (name or "").strip()
        if not _is_safe_module_name(name):
            logger.warning(f"[Modules] Пропуск недопустимого имени модуля: {name!r}")
            continue
        if not manager.should_autostart(name):
            logger.info(f"[Modules] Пропуск автозапуска модуля '{name}' (отключён).")
            continue

        module_path = f"{folder}.{name}.router"
        try:
            mod = importlib.import_module(module_path)
            router = getattr(mod, "router", None)
            if isinstance(router, Router):
                modules_hub.include_router(router)
                manager.adopt(name, router)
                routers.append(router)
                logger.info(f"[Modules] Загружен модуль: {module_path}")
            else:
                logger.warning(f"[Modules] В модуле {module_path} не найден router")
        except Exception as e:
            logger.error(f"[Modules] Ошибка при загрузке {module_path}: {e}")
    return routers


def load_module_webhooks(folder: str = "modules") -> list[dict]:
    webhooks = []
    base_path = Path(folder)

    for _finder, name, _ispkg in pkgutil.iter_modules([str(base_path)]):
        name = (name or "").strip()
        if not _is_safe_module_name(name):
            continue
        if not manager.should_autostart(name):
            logger.info(f"[Modules] Пропуск вебхуков модуля '{name}' (отключён).")
            continue

        module_path = f"{folder}.{name}"
        try:
            router_module = importlib.import_module(f"{module_path}.router")
            # В aiohttp регистрируется постоянный посредник, сам обработчик живёт в
            # среде модулей: после перезагрузки модуля запрос уходит в новый код.
            webhook_data = _register_legacy_webhook(name, router_module)
            if webhook_data:
                from core import module_runtime

                webhooks.append({**webhook_data, "handler": module_runtime.web_proxy(webhook_data["path"])})
                logger.info(f"[Modules] Найден вебхук в модуле {name}: {webhook_data['path']}")
        except Exception as e:
            logger.error(f"[Modules] Ошибка при загрузке вебхуков из {module_path}: {e}")
    return webhooks
