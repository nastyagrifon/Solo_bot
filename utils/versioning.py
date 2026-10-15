import os
import subprocess
import time

from logger import logger


#: Наш форк. У вендора (Vladless/Solo_bot) своя история и свои теги: ссылка на
#: его коммит по нашему хэшу ведёт в никуда.
REPO_URL = "https://github.com/nastyagrifon/Solo_bot"

#: Как долго помним ответ origin. Локальный хэш читается всегда заново - это
#: дешёвая операция без сети, и Настя просила видеть версию слота в момент
#: открытия меню (16.10.2026). А `git fetch` ходит в сеть, и делать его на
#: каждое нажатие кнопки нельзя: админка станет ждать секунды.
REMOTE_TTL_SEC = 3600

_remote_checked_at = 0.0
_remote_cache = ""


def _repo_dir() -> str | None:
    """Каталог репозитория, из которого запущен бот, или None.

    Проверяем `exists`, а НЕ `isdir`: при развёртывании слотами через
    `git worktree` `.git` в слоте - это ФАЙЛ со строкой
    `gitdir: <путь>/.git/worktrees/<слот>`. Старый код проверял `isdir`, не
    находил его и уходил на зашитый `/root/Solo_bot`, которого у нас нет -
    админка показывала «Требуется обновление через CLI (команда sudo solobot):
    [Errno 2] No such file or directory: '/root/Solo_bot'» (Настя, 16.10.2026).

    Зашитого пути больше нет совсем: каталог бота определяется только от
    расположения этого файла. Если репозитория рядом нет, версия выводится без
    git-части, а не с чужой ошибкой.
    """
    cwd = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    return cwd if os.path.exists(os.path.join(cwd, ".git")) else None


def _git(cwd: str, *args: str, timeout: int = 10) -> str:
    """git в каталоге репозитория.

    Через `-C`, без GIT_DIR и GIT_WORK_TREE в окружении: при worktree эти
    переменные указывают не туда, где лежит рабочая копия слота, и ломают
    команды, которые сами по себе работают.
    """
    return (
        subprocess.check_output(["git", "-C", cwd, *args], stderr=subprocess.DEVNULL, timeout=timeout)
        .decode()
        .strip()
    )


def _local_info(cwd: str) -> tuple[str, str, str]:
    """Хэш, короткий хэш и ветка текущего слота."""
    full = _git(cwd, "rev-parse", "HEAD")
    short = _git(cwd, "rev-parse", "--short", "HEAD")
    try:
        branch = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    except Exception:
        branch = "HEAD"
    if branch == "HEAD":
        # Слот ставится на конкретный ref, поэтому отделённая голова - норма.
        # Имя ветки берём из того, что на origin содержит этот коммит.
        try:
            refs = _git(cwd, "for-each-ref", "--contains", full, "--format=%(refname:short)", "refs/remotes/origin")
            names = [r.split("/", 1)[-1] for r in refs.splitlines() if r]
            branch = names[0] if names else "dev"
        except Exception:
            branch = "dev"
    return full, short, branch


def _remote_note(cwd: str, branch: str, local_hash: str) -> str:
    """Отстали ли мы от origin. Результат помнится REMOTE_TTL_SEC."""
    global _remote_checked_at, _remote_cache
    now = time.time()
    if now - _remote_checked_at < REMOTE_TTL_SEC:
        return _remote_cache

    _remote_checked_at = now
    try:
        line = _git(cwd, "ls-remote", "origin", f"refs/heads/{branch}", timeout=20)
        remote_hash = line.split()[0] if line else ""
    except Exception as exc:
        logger.warning("[Git] origin не ответил: {}", exc)
        _remote_cache = ""
        return _remote_cache

    if not remote_hash:
        _remote_cache = ""
    elif remote_hash == local_hash:
        _remote_cache = " · актуально"
    else:
        _remote_cache = (
            f' · <a href="{REPO_URL}/commit/{remote_hash}">на origin новее</a>,'
            " обновить: <code>slotctl install dev</code> → <code>slotctl switch</code>"
        )
    return _remote_cache


def get_git_commit_number() -> str:
    """Git-часть строки версии для админки. Пусто, если репозитория рядом нет."""
    cwd = _repo_dir()
    if cwd is None:
        return ""
    try:
        full, short, branch = _local_info(cwd)
    except Exception as exc:
        logger.warning("[Git] версия слота не прочиталась: {}", exc)
        return ""
    return f'\n<a href="{REPO_URL}/commit/{full}">{short}</a> ({branch}){_remote_note(cwd, branch, full)}'


def get_version(include_git_info: bool = True) -> str:
    base = "v6.0"
    if not include_git_info:
        return base
    return f"{base}{get_git_commit_number()}"
