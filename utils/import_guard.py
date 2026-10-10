"""Сторож пути импорта: ловит свой код, подхваченный из чужого дерева.

Любой модуль может вставить в sys.path посторонний каталог — например другой
клон проекта — и процесс начнёт брать оттуда часть собственных пакетов.
Ничего не падает и в журнале тишина: правки в рабочем дереве просто не
действуют. У нас так уехал весь пакет api, и выяснилось это только измерением
живого процесса.

Запретить такое ядро не может: sys.path общий. Зато может заметить и сказать.
"""

import sys
from pathlib import Path

from logger import logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_SHOW_LIMIT = 10


def _own_tops(root: Path) -> dict[str, Path]:
    """Свои пакеты и одиночные модули верхнего уровня с их настоящим местом.

    Место берём через resolve(): каталоги вроде modules или storage бывают
    симлинками на общее дерево, и код оттуда — свой, а не чужой.
    """
    tops: dict[str, Path] = {}
    try:
        entries = list(root.iterdir())
    except OSError:
        return tops
    for entry in entries:
        if entry.name.startswith((".", "__")):
            continue
        try:
            if entry.is_dir():
                # api у нас пакет-пространство имён, без __init__.py, поэтому
                # признак пакета — вообще наличие питоновских файлов внутри.
                if (entry / "__init__.py").exists() or any(entry.glob("*.py")):
                    tops[entry.name] = entry.resolve()
            elif entry.suffix == ".py":
                tops[entry.stem] = entry.resolve()
        except OSError:
            continue
    return tops


def foreign_modules(root: Path | None = None, modules: dict | None = None) -> dict[str, str]:
    """Загруженные модули проекта, которые лежат не там, где должны.

    Ключ — имя модуля, значение — путь, откуда он на самом деле взят.
    """
    base = (root or PROJECT_ROOT).resolve()
    tops = _own_tops(base)
    found: dict[str, str] = {}
    for name, module in list((modules if modules is not None else sys.modules).items()):
        top = name.split(".", 1)[0]
        expected = tops.get(top)
        if expected is None:
            continue
        source = getattr(module, "__file__", None)
        if not source:
            continue
        try:
            actual = Path(source).resolve()
        except OSError:
            continue
        if actual == expected or expected in actual.parents:
            continue
        found[name] = str(actual)
    return found


def warn_on_foreign_modules(where: str, root: Path | None = None) -> dict[str, str]:
    """Проверить и, если нашлось чужое, написать в журнал. Возвращает находку."""
    found = foreign_modules(root)
    if not found:
        return found

    by_top: dict[str, int] = {}
    for name in found:
        top = name.split(".", 1)[0]
        by_top[top] = by_top.get(top, 0) + 1
    summary = ", ".join(f"{top}: {count}" for top, count in sorted(by_top.items()))
    logger.error(
        "[ImportGuard] {} — код проекта загружен из чужого дерева ({}). "
        "Правки в рабочем дереве на эти модули не действуют.",
        where,
        summary,
    )
    for name in sorted(found)[:_SHOW_LIMIT]:
        logger.error("[ImportGuard]   {} <- {}", name, found[name])
    if len(found) > _SHOW_LIMIT:
        logger.error("[ImportGuard]   ... и ещё {}", len(found) - _SHOW_LIMIT)
    return found
