"""Роутеры, которые подключаются по требованию: импорт — только когда включены.

Для касс: выключенная касса не импортируется вовсе, включённая в админке на ходу
подключается при первом построении меню оплаты — без перезапуска бота.

    loader = LazyRouters(parent_router, package="handlers.payments")
    loader.add(".kassai", "KASSAI_CARDS", "KASSAI_SBP")
    loader.ensure(["KASSAI_SBP"])     # импорт handlers.payments.kassai и include_router

Повторный вызов — no-op; модуль, которого нет в сборке, даёт одно предупреждение.
"""

from __future__ import annotations

from collections.abc import Iterable
from importlib import import_module

from logger import logger


class LazyRouters:
    def __init__(self, parent, *, package: str, label: str = "Payments") -> None:
        self.parent = parent
        self.package = package
        self.label = label
        self._entries: dict[str, tuple[str, ...]] = {}
        self._loaded: set[str] = set()
        self._failed: set[str] = set()
        self._booted = False

    def add(self, module_path: str, *flags: str) -> None:
        """Роутер модуля ``module_path`` нужен, если включён любой из ``flags``."""
        self._entries[module_path] = tuple(flags)

    def ensure(self, enabled_flags: Iterable[str]) -> list[str]:
        """Подключить роутеры для включённых флагов, ещё не подключённые. Возвращает добавленные."""
        enabled = set(enabled_flags)
        added: list[str] = []
        for module_path, flags in self._entries.items():
            if module_path in self._loaded or module_path in self._failed or not enabled.intersection(flags):
                continue
            try:
                router = import_module(module_path, self.package).router
            except ImportError as e:
                self._failed.add(module_path)
                logger.warning("[{}] {} включена, но не входит в сборку: {}", self.label, "/".join(flags), e)
                continue
            self.parent.include_router(router)
            self._loaded.add(module_path)
            added.append(module_path)
            if self._booted:
                logger.info("[{}] {} подключена на лету", self.label, "/".join(flags))
        return added

    def boot(self, enabled_flags: Iterable[str]) -> list[str]:
        """Первая загрузка при старте; дальнейшие подключения логируются как «на лету»."""
        added = self.ensure(enabled_flags)
        self._booted = True
        return added

    def loaded(self) -> set[str]:
        return set(self._loaded)
