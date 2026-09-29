import importlib
import importlib.util
import json
import os
import sys

from aiogram import Router

from core.executor import run_io
from hooks.hooks import unregister_module_hooks
from logger import logger


IGNORE_SUBMODULES = {"models", "schemas", "db"}
STATE_FILE = os.getenv("MODULES_STATE_FILE", "storage/modules_state.json")


def _normalize_module_name(name: str | None) -> str:
    return (name or "").strip()


class ModuleRecord:
    def __init__(self, name: str, pkg: str) -> None:
        self.name = name
        self.pkg = pkg
        self.router: Router | None = None
        self.enabled: bool = False


class ModulesManager:
    def __init__(self, base: str = "modules") -> None:
        self.base = base
        self.registry: dict[str, ModuleRecord] = {}
        self.disabled: set[str] = set()
        self._load_state()

    def pkg(self, name: str) -> str:
        return f"{self.base}.{name}"

    def _load_state(self) -> None:
        try:
            if os.path.isfile(STATE_FILE):
                with open(STATE_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                raw = data.get("disabled", [])
                self.disabled = {_normalize_module_name(n) for n in raw if _normalize_module_name(n)}
            else:
                os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
                self._save_state()
        except Exception as e:
            logger.warning(f"[Modules] Не удалось загрузить состояние: {e}")

    def _save_state(self) -> None:
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"disabled": sorted(self.disabled)}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[Modules] Не удалось сохранить состояние: {e}")

    def adopt(self, name: str, router: Router):
        name = _normalize_module_name(name)
        rec = self.registry.get(name) or ModuleRecord(name, self.pkg(name))
        rec.router = router
        rec.enabled = True
        self.registry[name] = rec

    def _is_safe_module_name(self, name: str) -> bool:
        return bool(name and name.isidentifier() and "." not in name and "/" not in name and "\\" not in name)

    async def start(self, name: str) -> None:
        name = _normalize_module_name(name)
        if not self._is_safe_module_name(name):
            raise ValueError(f"[Modules] Недопустимое имя модуля: {name!r}")
        rec = self.registry.get(name) or ModuleRecord(name, self.pkg(name))
        if rec.enabled:
            logger.info(f"[Modules] {name} уже активен.")
            return

        try:
            unregister_module_hooks(name)
        except Exception:
            pass

        self.purge_selective(rec.pkg)
        _drop_bytecode(rec.pkg)

        mod = importlib.import_module(f"{rec.pkg}.router")
        router = getattr(mod, "router", None)
        if not isinstance(router, Router):
            raise RuntimeError(f"[Modules] В модуле {name} не найден router")

        from utils.modules_loader import modules_hub

        modules_hub.include_router(router)
        _register_legacy_webhook(name, mod)
        # Модуль грузится на живом боте: @router.startup() сам уже не сработает.
        await _emit_lifecycle(router, "startup", name)

        rec.router = router
        rec.enabled = True
        self.registry[name] = rec

        if name in self.disabled:
            self.disabled.discard(name)
            await run_io(self._save_state)

        logger.info(f"[Modules] {name} запущен.")

    async def stop(self, name: str) -> None:
        name = _normalize_module_name(name)
        rec = self.registry.get(name)
        if not rec or not rec.enabled:
            logger.info(f"[Modules] {name} уже остановлен или не найден.")
            if name not in self.disabled:
                self.disabled.add(name)
                await run_io(self._save_state)
            return

        try:
            unregister_module_hooks(name)
        except Exception:
            pass

        from utils.modules_loader import modules_hub

        await _emit_lifecycle(rec.router, "shutdown", name)

        sub = getattr(modules_hub, "_sub_routers", None) or getattr(modules_hub, "sub_routers", None)
        if sub and rec.router in sub:
            sub.remove(rec.router)

        # Всё остальное, что модуль завёл у бота: HTTP, middleware, задачи, откаты.
        from core import module_runtime

        await module_runtime.unload(name)

        rec.router = None
        rec.enabled = False

        if name not in self.disabled:
            self.disabled.add(name)
            await run_io(self._save_state)

        logger.info(f"[Modules] {name} остановлен.")

    async def restart(self, name: str) -> None:
        name = _normalize_module_name(name)
        logger.info(f"[Modules] Перезапуск {name}...")
        await self.stop(name)
        await self.start(name)

    def purge_selective(self, root_pkg: str) -> None:
        to_del = []
        for m in list(sys.modules):
            if m == root_pkg or m.startswith(root_pkg + "."):
                tail = m[len(root_pkg) :].lstrip(".")
                top = tail.split(".", 1)[0] if tail else ""
                if top and top in IGNORE_SUBMODULES:
                    continue
                to_del.append(m)
        for m in to_del:
            sys.modules.pop(m, None)
        importlib.invalidate_caches()

    def is_enabled(self, name: str) -> bool:
        name = _normalize_module_name(name)
        rec = self.registry.get(name)
        if not rec or not rec.router:
            return False
        try:
            from utils.modules_loader import modules_hub
        except Exception:
            return bool(rec.enabled)
        sub = getattr(modules_hub, "_sub_routers", None) or getattr(modules_hub, "sub_routers", None)
        return bool(sub and rec.router in sub)

    def is_disabled(self, name: str) -> bool:
        return _normalize_module_name(name) in self.disabled

    def should_autostart(self, name: str) -> bool:
        return _normalize_module_name(name) not in self.disabled


async def _emit_lifecycle(router: Router | None, event: str, name: str) -> None:
    """Вызвать обработчики @router.startup()/@router.shutdown() модуля на живом боте.

    aiogram зовёт их только при старте и остановке диспетчера. Модуль, загруженный
    или выгруженный на ходу, иначе не поставил бы свои подмены и не запустил бы
    фоновые циклы — или не остановил бы их. Ошибка модуля здесь не мешает загрузке.
    """
    if router is None:
        return
    # Только уже собранный бот: импорт bot.py отсюда запустил бы его сборку (тесты, старт).
    bot_mod = sys.modules.get("bot")
    bot, dp = getattr(bot_mod, "bot", None), getattr(bot_mod, "dp", None)
    if bot is None or dp is None:
        return
    try:
        emit = router.emit_startup if event == "startup" else router.emit_shutdown
        await emit(**{**dp.workflow_data, "bot": bot, "dispatcher": dp})
    except Exception as e:
        logger.error(f"[Modules] {name}: ошибка в обработчиках {event}: {e}")


def _drop_bytecode(pkg: str) -> None:
    """Удалить __pycache__ пакета модуля перед повторным импортом.

    Кеш байткода сверяется по времени изменения исходника с точностью до
    секунды и по размеру. Правка «того же размера в ту же секунду» (сменили
    одну цифру) иначе загрузит старый .pyc — перезагрузка молча отдаст
    прежний код.
    """
    import shutil

    spec = importlib.util.find_spec(pkg)
    if not spec or not spec.submodule_search_locations:
        return
    for location in spec.submodule_search_locations:
        for cache_dir in __import__("pathlib").Path(location).rglob("__pycache__"):
            shutil.rmtree(cache_dir, ignore_errors=True)


def _register_legacy_webhook(name: str, router_module) -> None:
    """``get_webhook_data()`` модуля → обработчик в среде модулей (актуальный после перезагрузки)."""
    getter = getattr(router_module, "get_webhook_data", None)
    if not callable(getter):
        return
    try:
        data = getter()
    except Exception as e:
        logger.error(f"[Modules] {name}: get_webhook_data упал: {e}")
        return
    if isinstance(data, dict) and data.get("path") and data.get("handler"):
        from core import module_runtime

        module_runtime.register_web(data["path"], data["handler"], module=name)


manager = ModulesManager()
