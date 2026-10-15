"""Обновление бота слотами.

Путь вендора (cli_launcher.py) обезврежен: его установщик перезаписывал
папку бота релизом из публичного репозитория и уносил наши правки, img/ и
модули, а обещанный «автоматический откат» возвращал сток, а не нас.

Здесь обновление идёт слотами: ref ставится в НЕАКТИВНЫЙ слот, переключение
без простоя, откат — обратным переключением. 16.10.2026, решение Насти.
"""

import asyncio
import html
import re
import os
import shlex
import shutil
import subprocess

from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.executor import run_io, spawn
from filters.admin import HasPermission, IsAdminFilter
from filters.permissions import PERM_MANAGEMENT
from logger import logger

from ..panel.headers import menu_text, note, quote, section
from ..panel.keyboard import AdminPanelCallback, build_admin_back_btn
from . import router


PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

#: Что ставим в неактивный слот: ветка нашего форка, а не тег вендора.
INSTALL_REF = "dev"

#: Отчёт лежит НАД каталогом проекта: переключение меняет активный слот, и
#: читать отчёт будет уже другой экземпляр бота из другого каталога.
REPORT_FILE = os.path.join(os.path.dirname(PROJECT_DIR), ".update_report")

#: Установка тянет ref и зависимости. Переключение ждёт /slot/ready.
INSTALL_TIMEOUT = 900.0
STATUS_TIMEOUT = 20.0

#: Юнит для отвязанного запуска: переключение гасит бота вместе со всем его
#: cgroup, поэтому команда должна жить в отдельном.
SLOTCTL_UNIT = "solobot-slotctl"

REPORT_WAIT_SECONDS = 300
REPORT_POLL_SECONDS = 5


def _installed_version() -> str:
    from core.rpc import local_version

    return local_version(PROJECT_DIR) or "неизвестна"


def _slotctl(args: list[str]) -> list[str]:
    """Команда slotctl. Бот обычно работает под root — тогда без sudo."""
    binary = shutil.which("slotctl") or "/usr/local/bin/slotctl"
    command = [binary, *args]
    if os.geteuid() != 0:
        command = ["sudo", "-n", *command]
    return command


def _run_slotctl(args: list[str], timeout: float) -> tuple[int, str]:
    """Синхронный запуск. Возвращает код и слитый вывод."""
    try:
        done = subprocess.run(
            _slotctl(args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    except FileNotFoundError:
        return 127, "slotctl не найден: установка не на слотах"
    except subprocess.TimeoutExpired:
        return 124, "не уложился в %d с" % int(timeout)
    return done.returncode, (done.stdout or b"").decode(errors="replace").strip()


def _launch_detached(args: list[str], admin_id: int) -> None:
    """Запуск, переживающий смерть бота, с отчётом в файл.

    Про телеграм slotctl не знает, поэтому отчёт пишем сами: сначала в .tmp,
    потом mv — иначе сторож прочитает недописанный файл.
    """
    quoted = " ".join(shlex.quote(part) for part in _slotctl(args))
    target = shlex.quote(REPORT_FILE)
    script = (
        'out=$(%s 2>&1); rc=$?; '
        '{ [ "$rc" = 0 ] && echo "ok %d" || echo "fail %d"; echo "$out"; } > %s.tmp; '
        'mv %s.tmp %s'
    ) % (quoted, admin_id, admin_id, target, target, target)
    command = ["sh", "-c", script]

    runner = shutil.which("systemd-run")
    if runner:
        prefix = [runner, "--unit", SLOTCTL_UNIT, "--collect", "--service-type=oneshot"]
        if os.geteuid() != 0:
            prefix = ["sudo", "-n", *prefix]
        done = subprocess.run(
            prefix + command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        if done.returncode == 0:
            return
        logger.warning("[Update] systemd-run не сработал: {}", (done.stderr or b"").decode(errors="replace").strip())

    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _slots(raw: str) -> list[dict]:
    """Слоты из вывода slotctl status: имя, активный ли, хэш коммита."""
    slots: list[dict] = []
    for line in raw.splitlines():
        parts = line.split()
        if not parts:
            continue
        active = parts[0] == "▶"
        if active:
            parts = parts[1:]
        if not parts or len(parts[0]) > 2:
            continue
        rest, commit = parts[1:], ""
        for index, part in enumerate(rest):
            if part.startswith("venv="):
                commit = rest[index + 1] if index + 1 < len(rest) else ""
                break
        slots.append({"name": parts[0], "active": active, "commit": commit})
    return slots


def _slot_date(name: str) -> str:
    """Дата коммита, лежащего в слоте: «16.10 22:51». Пусто, если git молчит."""
    path = os.path.join(os.path.dirname(PROJECT_DIR), name)
    try:
        done = subprocess.run(
            ["git", "-C", path, "log", "-1", "--format=%cd", "--date=format:%d.%m %H:%M"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (done.stdout or b"").decode(errors="replace").strip() if done.returncode == 0 else ""


def _inactive_slot(raw: str) -> str:
    """Имя неактивного слота."""
    for slot in _slots(raw):
        if not slot["active"]:
            return slot["name"]
    return ""


def _human_tail(out: str) -> str:
    """Последняя строка вывода slotctl без метки времени, коротко."""
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    if not lines:
        return ""
    tail = re.sub(r"^\d\d:\d\d:\d\d\s+", "", lines[-1])
    return tail[:90]


def _build_kb(inactive: str) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    if inactive:
        builder.row(
            InlineKeyboardButton(
                text=f"⬇️ Поставить {INSTALL_REF} в слот {inactive}",
                callback_data=AdminPanelCallback(action="update_install").pack(),
            )
        )
        builder.row(
            InlineKeyboardButton(
                text=f"🔀 Переключить на слот {inactive}",
                callback_data=AdminPanelCallback(action="update_switch").pack(),
            )
        )
        builder.row(
            InlineKeyboardButton(
                text="↩️ Откатить",
                callback_data=AdminPanelCallback(action="update_rollback").pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="🔄 Обновить статус",
            callback_data=AdminPanelCallback(action="update").pack(),
        )
    )
    builder.row(build_admin_back_btn("management"))
    return builder


def _screen_text(status: str, inactive: str) -> str:
    rows = []
    for slot in _slots(status):
        parts = ["▶️ активный"] if slot["active"] else []
        date = _slot_date(slot["name"])
        if date:
            parts.append(date)
        if slot["commit"]:
            parts.append(slot["commit"])
        rows.append("%s: %s" % (slot["name"], " · ".join(parts)))

    blocks = []
    if rows:
        blocks.append(section("🧩 Слоты", *rows))
    else:
        blocks.append(note("🧩 Слоты", html.escape(status or "slotctl молчит")))

    if inactive:
        blocks.append(
            note(
                "📋 Как это работает",
                f"Новое ставим в слот {inactive} — активный слот всё это время работает.",
                "Переключение переводит трафик, когда новый слот уже поднят и готов, "
                "поэтому клиенты его не замечают. Не поднялся — остаёмся на текущем.",
                "«Откатить» возвращает прежний слот тем же переключением.",
            )
        )
    else:
        blocks.append(note("📋 Как это работает", "Слоты не найдены: обновление только руками, slotctl на сервере."))
    return menu_text("Обновление", *blocks)


async def _render(callback: CallbackQuery) -> None:
    code, status = await run_io(lambda: _run_slotctl(["status"], STATUS_TIMEOUT))
    if code != 0:
        logger.warning("[Update] slotctl status вернул {}: {}", code, status)
    inactive = _inactive_slot(status) if code == 0 else ""
    markup = _build_kb(inactive).as_markup()
    await callback.message.edit_text(text=_screen_text(status, inactive), reply_markup=markup)
    await callback.answer()


@router.callback_query(
    AdminPanelCallback.filter(F.action == "update"),
    HasPermission(PERM_MANAGEMENT),
    IsAdminFilter(),
)
async def open_update(callback: CallbackQuery) -> None:
    await _render(callback)


@router.callback_query(
    AdminPanelCallback.filter(F.action == "update_install"),
    HasPermission(PERM_MANAGEMENT),
    IsAdminFilter(),
)
async def install_slot(callback: CallbackQuery) -> None:
    """slotctl install: долго, но бот жив — ждём прямо здесь."""
    await callback.answer("Ставлю, это несколько минут")
    kb = InlineKeyboardBuilder()
    kb.row(build_admin_back_btn("update"))
    await callback.message.edit_text(
        text=menu_text(
            "Обновление",
            f"⏳ Ставлю <b>{INSTALL_REF}</b> в неактивный слот.",
            quote("Активный слот работает как обычно."),
        ),
        reply_markup=kb.as_markup(),
    )

    code, out = await run_io(lambda: _run_slotctl(["install", INSTALL_REF], INSTALL_TIMEOUT))
    logger.info("[Update] slotctl install {} вернул {}", INSTALL_REF, code)
    if code == 0:
        body = note("Готово", html.escape(_human_tail(out) or "слот обновлён"))
        title = "✅ Поставлено"
    else:
        # При отказе показываем вывод целиком: причина только там.
        body = note("Вывод slotctl", html.escape("\n".join(out.splitlines()[-12:]) or "вывода нет"))
        title = f"❌ Установка не удалась (код {code})"
    await callback.message.edit_text(
        text=menu_text("Обновление", title, body),
        reply_markup=kb.as_markup(),
    )


def _confirm_kb(action: str) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Да", callback_data=AdminPanelCallback(action=action).pack()),
        InlineKeyboardButton(text="✖️ Отмена", callback_data=AdminPanelCallback(action="update").pack()),
    )
    return builder


@router.callback_query(
    AdminPanelCallback.filter(F.action.in_({"update_switch", "update_rollback"})),
    HasPermission(PERM_MANAGEMENT),
    IsAdminFilter(),
)
async def confirm_move(callback: CallbackQuery, callback_data: AdminPanelCallback) -> None:
    rollback = callback_data.action == "update_rollback"
    title = "↩️ Откатить на прежний слот?" if rollback else "🔀 Переключить слот?"
    await callback.message.edit_text(
        text=menu_text(
            "Обновление",
            title,
            quote(
                "Новый слот поднимется, трафик перейдёт на него, старый остановится. "
                "Перевод идёт только после готовности нового слота.",
                "Отчёт придёт сюда же.",
            ),
        ),
        reply_markup=_confirm_kb(f"{callback_data.action}_go").as_markup(),
    )
    await callback.answer()


@router.callback_query(
    AdminPanelCallback.filter(F.action.in_({"update_switch_go", "update_rollback_go"})),
    HasPermission(PERM_MANAGEMENT),
    IsAdminFilter(),
)
async def move_slot(callback: CallbackQuery, callback_data: AdminPanelCallback) -> None:
    command = "rollback" if callback_data.action.startswith("update_rollback") else "switch"
    await callback.answer()
    kb = InlineKeyboardBuilder()
    kb.row(build_admin_back_btn("management"))
    await callback.message.edit_text(
        text=menu_text(
            "Обновление",
            "⏳ Откатываю слот." if command == "rollback" else "⏳ Переключаю слот.",
            quote("Поднимаю слот, жду готовности, перевожу трафик.", "Отчёт пришлю сюда же."),
        ),
        reply_markup=kb.as_markup(),
    )

    try:
        os.unlink(REPORT_FILE)
    except OSError:
        pass

    logger.info("[Update] Отвязанный запуск slotctl {}", command)
    spawn(_run_move(command, callback.from_user.id))


async def _run_move(command: str, admin_id: int) -> None:
    await asyncio.sleep(1)
    try:
        await run_io(lambda: _launch_detached([command], admin_id))
    except Exception as error:
        logger.error("[Update] Не удалось запустить slotctl {}: {}", command, error)


def take_update_report() -> tuple[str, int, str] | None:
    """Отчёт переключения: статус, кому доложить, вывод. Читается один раз."""
    try:
        with open(REPORT_FILE, encoding="utf-8") as fh:
            head = fh.readline().split()
            body = fh.read().strip()
    except OSError:
        return None
    try:
        os.unlink(REPORT_FILE)
    except OSError:
        pass
    if len(head) != 2 or not head[1].isdigit():
        logger.warning("[Update] Отчёт испорчен, пропускаю")
        return None
    return head[0], int(head[1]), body


def format_report(status: str, body: str) -> str:
    if status == "ok":
        return menu_text(
            "Обновление",
            "✅ Переключение прошло",
            note("Готово", html.escape(_human_tail(body) or "слот переключён")),
        )
    return menu_text(
        "Обновление",
        "❌ Переключение не удалось",
        note("Вывод slotctl", html.escape("\n".join(body.splitlines()[-12:]) or "вывода нет")),
    )


async def report_update_result(bot) -> None:
    """Доклад админу после переключения слота.

    Команда работает в отдельном юните и пишет отчёт, уже когда новый слот
    поднялся — на момент старта файла ещё нет, поэтому ждём его в фоне.
    """
    spawn(_watch_report(bot))


async def _watch_report(bot) -> None:
    deadline = REPORT_WAIT_SECONDS
    while deadline > 0:
        report = take_update_report()
        if report is not None:
            status, admin_id, body = report
            try:
                await bot.send_message(admin_id, format_report(status, body))
            except Exception as error:
                logger.error("[Update] Не удалось отправить отчёт: {}", error)
            return
        await asyncio.sleep(REPORT_POLL_SECONDS)
        deadline -= REPORT_POLL_SECONDS
