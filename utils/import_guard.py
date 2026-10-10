"""Сторож пути импорта: ловит свой код, подхваченный из чужого дерева.

Любой модуль может вставить в sys.path посторонний каталог — например другой
клон проекта — и процесс начнёт брать оттуда часть собственных пакетов.
Ничего не падает и в журнале тишина: правки в рабочем дереве просто не
действуют. У нас так уехал весь пакет api, и выяснилось это только измерением
живого процесса.

Запретить такое ядро не может: sys.path общий. Зато может заметить и сказать.

Решает один вопрос: лежит ли модуль там, где ему положено по его же имени.
Имя переводится в путь внутри корня проекта, и если такой файл есть, его
настоящее место (через resolve, то есть сквозь симлинки) обязано совпасть с
тем, откуда модуль взят. Нет такого файла — значит это не наш модуль вовсе:
библиотека с совпавшим именем, алиас или скомпилированное расширение.
"""

import sys
import sysconfig
from pathlib import Path

from logger import logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_SHOW_LIMIT = 10

# Эти два имени Python импортирует сам при запуске интерпретатора, задолго до
# нашего кода, и берёт их из системных каталогов. Одноимённый файл в корне
# проекта к ним отношения не имеет.
_INTERPRETER_MODULES = frozenset({"sitecustomize", "usercustomize"})


def _library_dirs() -> tuple[Path, ...]:
    """Каталоги установленных пакетов и стандартной библиотеки.

    Имя своего каталога может совпасть с именем библиотеки: у нас совпали
    каталог миграций alembic и сам alembic из окружения.
    """
    paths = sysconfig.get_paths()
    dirs = []
    for key in ("purelib", "platlib", "stdlib", "platstdlib"):
        raw = paths.get(key)
        if not raw:
            continue
        try:
            dirs.append(Path(raw).resolve())
        except OSError:
            continue
    return tuple(dict.fromkeys(dirs))


LIBRARY_DIRS = _library_dirs()


def _expected_places(root: Path, name: str) -> tuple[Path, ...]:
    """Где модулю с таким именем положено лежать внутри корня проекта."""
    stem = root.joinpath(*name.split("."))
    return (stem.with_suffix(".py"), stem / "__init__.py")


def foreign_modules(
    root: Path | None = None,
    modules: dict | None = None,
    library_dirs: tuple[Path, ...] | None = None,
) -> dict[str, str]:
    """Загруженные модули проекта, взятые не из своего дерева.

    Ключ — имя модуля, значение — путь, откуда он на самом деле взят.
    """
    base = (root or PROJECT_ROOT).resolve()
    libs = LIBRARY_DIRS if library_dirs is None else library_dirs

    suspects: list[tuple[str, Path]] = []
    legit: set[Path] = set()

    for name, module in list((modules if modules is not None else sys.modules).items()):
        if name in _INTERPRETER_MODULES:
            continue
        source = getattr(module, "__file__", None)
        if not source:
            continue
        try:
            actual = Path(source).resolve()
        except OSError:
            continue
        if any(lib == actual or lib in actual.parents for lib in libs):
            continue

        places = [place for place in _expected_places(base, name) if place.exists()]
        if not places:
            continue
        if any(place.resolve() == actual for place in places):
            legit.add(actual)
            continue
        suspects.append((name, actual))

    # Один и тот же файл бывает зарегистрирован под вторым именем (ядро так
    # кладёт settings.config ещё и как config). Подменой это не является:
    # сам файл уже признан своим под настоящим именем.
    return {name: str(actual) for name, actual in suspects if actual not in legit}


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
