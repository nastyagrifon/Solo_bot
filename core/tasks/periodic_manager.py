import asyncio
import fcntl
import inspect
import multiprocessing
import os
import tempfile
import threading
import time

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.executors.pool import ProcessPoolExecutor as APSchedulerProcessPoolExecutor
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.base import BaseTrigger
from sqlalchemy.ext.asyncio import async_sessionmaker

from logger import logger


LoopRunner = Callable[[Bot, async_sessionmaker], Awaitable[None]]
ThreadLoopRunner = Callable[[threading.Event, Bot, async_sessionmaker], None]
CronRunner = Callable[[], Awaitable[None]] | Callable[[], None]
CronExecutionMode = Literal["async", "process"]
_LOOP_RESTART_BASE_DELAY_SEC = 5
_LOOP_RESTART_MAX_DELAY_SEC = 300
_LOOP_RESTART_STABLE_RUNTIME_SEC = 300


def _module_task_owner(task_id: str) -> str | None:
    """Имя модуля для цикла вида module:<имя>:<id> (см. discover_module_loop_tasks)."""
    prefix, _, rest = task_id.partition(":")
    name = rest.partition(":")[0]
    return name if prefix == "module" and name else None


def _loop_restart_delay(failures: int) -> int:
    return min(_LOOP_RESTART_BASE_DELAY_SEC * (2 ** min(failures, 6)), _LOOP_RESTART_MAX_DELAY_SEC)


@dataclass
class ManagedLoopTask:
    task_id: str
    runner: LoopRunner


@dataclass
class ManagedThreadLoopTask:
    task_id: str
    runner: ThreadLoopRunner


@dataclass
class ManagedProcessLoopTask:
    task_id: str
    runner: LoopRunner


@dataclass
class ManagedCronTask:
    task_id: str
    runner: CronRunner
    trigger: BaseTrigger
    execution_mode: CronExecutionMode
    args: tuple = ()


@dataclass
class RunningThreadLoopTask:
    thread: threading.Thread
    stop_event: threading.Event


@dataclass
class RunningProcessLoopTask:
    process: multiprocessing.Process | None
    monitor_task: asyncio.Task | None = None


def _run_process_loop_task(task_id: str, runner: LoopRunner) -> None:
    asyncio.run(_run_process_loop_task_async(task_id, runner))


async def _run_process_loop_task_async(task_id: str, runner: LoopRunner) -> None:
    from core.bootstrap import bootstrap
    from core.settings.modes_config import resolve_protect_content
    from core.settings.runtime_sync import runtime_config_sync_loop
    from database import async_session_maker
    from database.db import reset_async_db_engine
    from settings.config import API_TOKEN

    bot = Bot(
        token=API_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, protect_content=resolve_protect_content()),
    )
    logger.info("[PeriodicManager] Process-loop задача {} запущена, PID={}", task_id, os.getpid())
    sync_task = None
    try:
        reset_async_db_engine()
        await bootstrap()
        if bot.default is not None:
            bot.default.protect_content = resolve_protect_content()
        # Апдейтов в этом процессе нет: правки админа подтягивает только свой опрос.
        sync_task = asyncio.create_task(runtime_config_sync_loop())
        await runner(bot, async_session_maker)
    except Exception as error:
        logger.opt(exception=error).error("[PeriodicManager] Ошибка process-loop задачи {}", task_id)
        raise
    finally:
        if sync_task is not None:
            sync_task.cancel()
        try:
            await bot.session.close()
        except Exception:
            pass


class PeriodicTaskManager:
    def __init__(self, timezone_name: str = "Europe/Moscow") -> None:
        self.timezone_name = timezone_name
        self._loop_tasks: dict[str, ManagedLoopTask] = {}
        self._thread_loop_tasks: dict[str, ManagedThreadLoopTask] = {}
        self._process_loop_tasks: dict[str, ManagedProcessLoopTask] = {}
        self._cron_tasks: dict[str, ManagedCronTask] = {}
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._running_thread_tasks: dict[str, RunningThreadLoopTask] = {}
        self._running_process_tasks: dict[str, RunningProcessLoopTask] = {}
        self._scheduler: AsyncIOScheduler | None = None
        self._scheduler_process_workers: int | None = None
        self._started = False
        self._lock = asyncio.Lock()
        self._process_lock_file = None
        self._cached_instance_key: str | None = None
        self._leader_wait: asyncio.Task | None = None
        self._process_lock_path = os.path.join(tempfile.gettempdir(), "solo_bot_periodic_manager.lock")

    def _instance_key(self) -> str:
        if self._cached_instance_key is not None:
            return self._cached_instance_key
        import hashlib

        parts: list[str] = []
        try:
            from settings.config import API_TOKEN

            parts.append(str(API_TOKEN or ""))
        except Exception:
            pass
        # Без пути к коду: слоты A/B одного бота живут в разных каталогах, а лидер периодики нужен один.
        raw = "|".join(part for part in parts if part) or "default"
        self._cached_instance_key = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        return self._cached_instance_key

    def _process_lock_candidates(self) -> list[str]:
        filename = f"solo_bot_periodic_manager_{os.getuid()}_{self._instance_key()}.lock"
        candidates: list[str] = []
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "").strip()
        if runtime_dir:
            candidates.append(os.path.join(runtime_dir, filename))
        candidates.append(os.path.join(tempfile.gettempdir(), filename))
        unique_candidates: list[str] = []
        for candidate in candidates:
            if candidate not in unique_candidates:
                unique_candidates.append(candidate)
        return unique_candidates

    def register_loop_task(self, task_id: str, runner: LoopRunner) -> None:
        self._loop_tasks[task_id] = ManagedLoopTask(task_id=task_id, runner=runner)

    def register_thread_loop_task(self, task_id: str, runner: ThreadLoopRunner) -> None:
        self._thread_loop_tasks[task_id] = ManagedThreadLoopTask(task_id=task_id, runner=runner)

    def register_process_loop_task(self, task_id: str, runner: LoopRunner) -> None:
        self._process_loop_tasks[task_id] = ManagedProcessLoopTask(task_id=task_id, runner=runner)

    def set_scheduler_process_workers(self, workers: int | None) -> None:
        self._scheduler_process_workers = None if workers is None else max(0, int(workers))

    def register_cron_task(
        self,
        task_id: str,
        runner: CronRunner,
        trigger: BaseTrigger,
        execution_mode: CronExecutionMode = "async",
        args: tuple = (),
    ) -> None:
        if execution_mode not in {"async", "process"}:
            raise ValueError(f"Unsupported execution_mode: {execution_mode}")
        if execution_mode != "async" and inspect.iscoroutinefunction(runner):
            raise ValueError(f"Cron task {task_id} with execution_mode={execution_mode} must be a sync function")
        self._cron_tasks[task_id] = ManagedCronTask(
            task_id=task_id,
            runner=runner,
            trigger=trigger,
            execution_mode=execution_mode,
            args=args,
        )

    def _acquire_process_lock(self) -> bool:
        if self._process_lock_file is not None:
            return True
        for candidate_path in self._process_lock_candidates():
            try:
                lock_file = open(candidate_path, "a+", encoding="utf-8")
            except OSError as error:
                logger.warning("[PeriodicManager] Не удалось открыть lock-файл {}: {}", candidate_path, error)
                continue
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                lock_file.seek(0)
                lock_file.truncate()
                lock_file.write(str(os.getpid()))
                lock_file.flush()
                self._process_lock_file = lock_file
                self._process_lock_path = candidate_path
                return True
            except OSError:
                lock_file.close()
                return False
        logger.warning("[PeriodicManager] Не удалось создать lock-файл, запуск менеджера пропущен")
        return False

    def _release_process_lock(self) -> None:
        if self._process_lock_file is None:
            return
        try:
            fcntl.flock(self._process_lock_file.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            self._process_lock_file.close()
        except OSError:
            pass
        self._process_lock_file = None

    async def _run_async_loop_task(
        self,
        loop_task: ManagedLoopTask,
        bot: Bot,
        sessionmaker: async_sessionmaker,
    ) -> None:
        failures = 0
        while True:
            started_at = time.monotonic()
            try:
                await loop_task.runner(bot, sessionmaker)
                logger.warning("[PeriodicManager] Цикл {} завершился", loop_task.task_id)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.opt(exception=error).error("[PeriodicManager] Цикл {} упал", loop_task.task_id)
            if time.monotonic() - started_at >= _LOOP_RESTART_STABLE_RUNTIME_SEC:
                failures = 0
            delay = _loop_restart_delay(failures)
            failures = min(failures + 1, 6)
            logger.warning(
                "[PeriodicManager] Цикл {} перезапустится через {} сек.",
                loop_task.task_id,
                delay,
            )
            await asyncio.sleep(delay)

    def _build_scheduler(self) -> AsyncIOScheduler:
        from settings.config import PROCESS_POOL_SIZE

        configured_process_workers = self._scheduler_process_workers
        if configured_process_workers is None:
            configured_process_workers = int(PROCESS_POOL_SIZE)
        process_workers = max(0, min(configured_process_workers, multiprocessing.cpu_count() or 1))
        executors = {"default": AsyncIOExecutor()}
        if process_workers > 0:
            executors["processpool"] = APSchedulerProcessPoolExecutor(max_workers=process_workers)
        return AsyncIOScheduler(timezone=self.timezone_name, executors=executors)

    @staticmethod
    def _thread_loop_entry(
        task_id: str,
        runner: ThreadLoopRunner,
        stop_event: threading.Event,
        bot: Bot,
        sessionmaker: async_sessionmaker,
    ) -> None:
        failures = 0
        while not stop_event.is_set():
            started_at = time.monotonic()
            try:
                runner(stop_event, bot, sessionmaker)
                if stop_event.is_set():
                    return
                logger.warning("[PeriodicManager] Thread-loop задача {} завершилась", task_id)
            except Exception as error:
                logger.opt(exception=error).error("[PeriodicManager] Ошибка thread-loop задачи {}", task_id)
            if time.monotonic() - started_at >= _LOOP_RESTART_STABLE_RUNTIME_SEC:
                failures = 0
            delay = _loop_restart_delay(failures)
            failures = min(failures + 1, 6)
            logger.warning(
                "[PeriodicManager] Thread-loop задача {} перезапустится через {} сек.",
                task_id,
                delay,
            )
            if stop_event.wait(delay):
                return

    async def _join_thread_task(self, task_id: str, running_task: RunningThreadLoopTask) -> None:
        running_task.stop_event.set()
        await asyncio.to_thread(running_task.thread.join, 5)
        if running_task.thread.is_alive():
            logger.warning("[PeriodicManager] Thread-loop задача {} не завершилась вовремя", task_id)

    async def _stop_process_task(self, task_id: str, running_task: RunningProcessLoopTask) -> None:
        process = running_task.process
        if process is None:
            return
        if not process.is_alive():
            await asyncio.to_thread(process.join, 1)
            return
        process.terminate()
        await asyncio.to_thread(process.join, 5)
        if process.is_alive():
            process.kill()
            await asyncio.to_thread(process.join, 5)
        if process.is_alive():
            logger.warning("[PeriodicManager] Process-loop задача {} не завершилась вовремя", task_id)

    async def _watch_process_loop(
        self,
        loop_task: ManagedProcessLoopTask,
        running_task: RunningProcessLoopTask,
    ) -> None:
        failures = 0
        started_at = time.monotonic()
        while self._started:
            process = running_task.process
            if process is not None:
                while process.is_alive() and self._started:
                    await asyncio.sleep(1)
                if not self._started:
                    return
                exit_code = process.exitcode
                await asyncio.to_thread(process.join, 1)
                running_task.process = None
                if time.monotonic() - started_at >= _LOOP_RESTART_STABLE_RUNTIME_SEC:
                    failures = 0
                if exit_code == 0:
                    logger.warning("[PeriodicManager] Process-loop задача {} завершилась", loop_task.task_id)
                else:
                    logger.error(
                        "[PeriodicManager] Process-loop задача {} завершилась с кодом {}",
                        loop_task.task_id,
                        exit_code,
                    )

            delay = _loop_restart_delay(failures)
            failures = min(failures + 1, 6)
            logger.warning(
                "[PeriodicManager] Process-loop задача {} перезапустится через {} сек.",
                loop_task.task_id,
                delay,
            )
            await asyncio.sleep(delay)
            if not self._started:
                return

            try:
                ctx = multiprocessing.get_context("spawn")
                process = ctx.Process(
                    target=_run_process_loop_task,
                    args=(loop_task.task_id, loop_task.runner),
                    name=f"periodic-{loop_task.task_id}",
                    daemon=True,
                )
                process.start()
                running_task.process = process
                started_at = time.monotonic()
            except Exception as error:
                logger.opt(exception=error).error(
                    "[PeriodicManager] Не удалось перезапустить process-loop задачу {}",
                    loop_task.task_id,
                )

    async def start(self, bot: Bot, sessionmaker: async_sessionmaker) -> None:
        async with self._lock:
            if self._started:
                return
            if not self._acquire_process_lock():
                if self._leader_wait is None:
                    logger.info("[PeriodicManager] Периодику ведёт другой процесс, ждём, пока он её отпустит")
                    self._leader_wait = asyncio.create_task(self._wait_for_leadership(bot, sessionmaker))
                return
            scheduler = self._build_scheduler()
            for cron_task in self._cron_tasks.values():
                scheduler.add_job(
                    cron_task.runner,
                    cron_task.trigger,
                    args=cron_task.args,
                    name=cron_task.task_id if cron_task.args else None,  # иначе все процессные — "run_in_own_loop"
                    id=cron_task.task_id,
                    executor="processpool" if cron_task.execution_mode == "process" else "default",
                    replace_existing=True,
                    max_instances=1,
                    coalesce=True,
                )
            scheduler.start()
            self._scheduler = scheduler
            for loop_task in self._loop_tasks.values():
                coro = self._run_async_loop_task(loop_task, bot, sessionmaker)
                owner = _module_task_owner(loop_task.task_id)
                if owner:
                    from core import module_runtime

                    # На учёте модуля: его остановка снимает и цикл.
                    task = module_runtime.spawn(coro, module=owner)
                else:
                    task = asyncio.create_task(coro)
                self._running_tasks[loop_task.task_id] = task
            for loop_task in self._thread_loop_tasks.values():
                stop_event = threading.Event()
                thread = threading.Thread(
                    target=self._thread_loop_entry,
                    args=(loop_task.task_id, loop_task.runner, stop_event, bot, sessionmaker),
                    name=f"periodic-{loop_task.task_id}",
                    daemon=True,
                )
                thread.start()
                self._running_thread_tasks[loop_task.task_id] = RunningThreadLoopTask(
                    thread=thread,
                    stop_event=stop_event,
                )
            for loop_task in self._process_loop_tasks.values():
                ctx = multiprocessing.get_context("spawn")
                process = ctx.Process(
                    target=_run_process_loop_task,
                    args=(loop_task.task_id, loop_task.runner),
                    name=f"periodic-{loop_task.task_id}",
                    daemon=True,
                )
                process.start()
                running_task = RunningProcessLoopTask(process=process)
                self._running_process_tasks[loop_task.task_id] = running_task
                running_task.monitor_task = asyncio.create_task(self._watch_process_loop(loop_task, running_task))
            self._started = True
            logger.info(
                "[PeriodicManager] Запущен: async-loop=%s thread-loop=%s process-loop=%s cron=%s",
                len(self._loop_tasks),
                len(self._thread_loop_tasks),
                len(self._process_loop_tasks),
                len(self._cron_tasks),
            )

    async def _wait_for_leadership(self, bot: Bot, sessionmaker: async_sessionmaker, interval: float = 5.0) -> None:
        """Второй слот ждёт, пока первый отпустит блокировку (ядро снимает flock и при аварийной смерти)."""
        while not self._acquire_process_lock():
            await asyncio.sleep(interval)
        self._leader_wait = None  # блокировку держим: start() увидит её и не отдаст в щель между вызовами
        logger.info("[PeriodicManager] Блокировка свободна, принимаю периодику")
        await self.start(bot, sessionmaker)

    async def stop(self) -> None:
        if self._leader_wait is not None:
            self._leader_wait.cancel()
            self._leader_wait = None
        async with self._lock:
            if not self._started:
                return
            self._started = False
            if self._scheduler is not None:
                try:
                    self._scheduler.shutdown(wait=False)
                except Exception:
                    pass
                self._scheduler = None
            tasks = list(self._running_tasks.values())
            self._running_tasks.clear()
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            thread_tasks = list(self._running_thread_tasks.items())
            self._running_thread_tasks.clear()
            if thread_tasks:
                await asyncio.gather(
                    *(self._join_thread_task(task_id, running_task) for task_id, running_task in thread_tasks),
                    return_exceptions=True,
                )
            process_tasks = list(self._running_process_tasks.items())
            self._running_process_tasks.clear()
            if process_tasks:
                monitor_tasks = [
                    running_task.monitor_task
                    for _, running_task in process_tasks
                    if running_task.monitor_task is not None
                ]
                for monitor_task in monitor_tasks:
                    monitor_task.cancel()
                if monitor_tasks:
                    await asyncio.gather(*monitor_tasks, return_exceptions=True)
                await asyncio.gather(
                    *(self._stop_process_task(task_id, running_task) for task_id, running_task in process_tasks),
                    return_exceptions=True,
                )
            self._release_process_lock()
            logger.info("[PeriodicManager] Остановлен")


periodic_task_manager = PeriodicTaskManager()
