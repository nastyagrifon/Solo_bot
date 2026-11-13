import asyncio
from functools import partial

from collections.abc import Callable, Coroutine

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from database import async_session_maker, cancel_expired_pending_payments
from logger import logger


async def scheduled_audit_drain() -> None:
    from audit import drain_audit_redis_to_db

    try:
        drained = await drain_audit_redis_to_db(async_session_maker)
        logger.info("[AuditDrain] Ночной drain завершён, записано событий: {}", drained)
    except Exception as error:
        logger.error("[AuditDrain] Ошибка ночного drain: {}", error)


async def scheduled_audit_drain_if_long() -> None:
    """Разбирает очередь аудита, не дожидаясь полуночи, если она разрослась.

    Раз в сутки мало: 11.10.2026 одна фоновая вкладка кабинета с протухшей
    сессией нагнала 469 тысяч записей за день. Потолок очереди спасает Redis,
    но ценой потери событий — лучше сливать их в базу по мере накопления.
    Проверка дешёвая (один LLEN), слив идёт только при превышении порога.
    """
    from audit import AUDIT_REDIS_FLUSH_KEY, drain_audit_redis_to_db
    from core.redis_cache import cache_llen
    from settings.cache_config import AUDIT_DRAIN_LENGTH_THRESHOLD

    try:
        length = await cache_llen(AUDIT_REDIS_FLUSH_KEY)
        if length < AUDIT_DRAIN_LENGTH_THRESHOLD:
            return
        logger.info("[AuditDrain] Очередь выросла до {}, разбираю не дожидаясь ночи", length)
        drained = await drain_audit_redis_to_db(async_session_maker)
        logger.info("[AuditDrain] Внеочередной drain завершён, записано событий: {}", drained)
    except Exception as error:
        logger.error("[AuditDrain] Ошибка внеочередного drain: {}", error)


async def scheduled_stats_report() -> None:
    from handlers.admin.stats.stats_handler import send_daily_stats_report, stats_report_target

    chat_id, thread_id = stats_report_target()
    async with async_session_maker() as session:
        await send_daily_stats_report(session, chat_id=chat_id, thread_id=thread_id)


async def scheduled_monthly_stats_report() -> None:
    from handlers.admin.stats.stats_handler import send_monthly_stats_report, stats_report_target

    chat_id, thread_id = stats_report_target()
    async with async_session_maker() as session:
        await send_monthly_stats_report(session, chat_id=chat_id, thread_id=thread_id)


async def sweep_stale_payments_job() -> None:
    async with async_session_maker() as session:
        await cancel_expired_pending_payments(session)
        await session.commit()


def run_in_own_loop(job: Callable[[], Coroutine[object, object, object]]) -> None:
    """Запускает задачу в отдельном цикле событий с новым движком базы данных."""
    from database.db import reset_async_db_engine

    reset_async_db_engine()
    # Сначала свежие настройки, затем закрытие клиента Redis и движка прогона.
    asyncio.run(_run_and_release(partial(_with_fresh_runtime_configs, job)))


async def _run_and_release(job: Callable[[], Coroutine[object, object, object]]) -> None:
    # Воркер пула живёт долго, а цикл у каждой задачи свой: клиент Redis и движок
    # этого цикла без закрытия копятся в воркере (задача раз в 10 минут — 144 в сутки).
    from core.redis_cache import close_loop_client
    from database import db

    try:
        await job()
    finally:
        try:
            await close_loop_client()
        finally:
            await db.engine.dispose()


async def _with_fresh_runtime_configs(job: Callable[[], Coroutine[object, object, object]]) -> None:
    # Воркер пула живёт долго и апдейтов не видит: без этого задача берёт *_CONFIG на момент его старта.
    from core.settings.runtime_sync import maybe_sync_runtime_configs

    try:
        await maybe_sync_runtime_configs(force=True)
    except Exception as error:
        logger.warning("[Cron] Правки настроек не подтянуты: {}", error)
    await job()


async def cleanup_expired_gifts_job() -> None:
    from datetime import datetime

    from sqlalchemy import update as sa_update

    from database.models import Gift

    async with async_session_maker() as session:
        try:
            result = await session.execute(
                sa_update(Gift).where(Gift.expiry_time < datetime.utcnow(), Gift.is_used is False).values(is_used=True)
            )
            count = result.rowcount
            await session.commit()
            if count:
                logger.info("[GiftCleanup] Просроченных подарков помечено использованными: {}", count)
        except Exception as error:
            logger.error("[GiftCleanup] Ошибка очистки подарков: {}", error)


async def autoclose_stale_tickets_job() -> None:
    from services.tickets import close_stale

    async with async_session_maker() as session:
        try:
            count = await close_stale(session, answered_days=2)
            await session.commit()
            if count:
                logger.info("[TicketsCleanup] Автозакрыто обращений без ответа клиента: {}", count)
        except Exception as error:
            logger.error("[TicketsCleanup] Ошибка автозакрытия обращений: {}", error)


WEB_ANALYTICS_RETENTION_DAYS = 90
WEB_ERROR_RETENTION_DAYS = 30
AUDIT_RETENTION_DAYS = 90


async def cleanup_web_analytics_job() -> None:
    """Удаляет старую веб-аналитику, чтобы таблицы не росли бесконечно."""
    from datetime import (
        datetime,
        timedelta,
        timezone as _tz,
    )

    from sqlalchemy import (
        delete as sa_delete,
        text as _sa_text,
    )

    from audit import delete_old_audit_events
    from database.models import KeyTrafficHistory
    from database.models.web import WebErrorReport, WebFlowEvent, WebPageView

    now = datetime.now(_tz.utc)
    analytics_cutoff = now - timedelta(days=WEB_ANALYTICS_RETENTION_DAYS)
    error_cutoff = now - timedelta(days=WEB_ERROR_RETENTION_DAYS)
    traffic_cutoff = (now - timedelta(days=180)).date()
    rate_limit_cutoff = int(now.timestamp()) - 3600

    async def rows_deleted(session, stmt, params: dict | None = None) -> int:
        result = await session.execute(stmt, params) if params else await session.execute(stmt)
        return result.rowcount or 0

    steps: tuple[tuple[str, Callable[[object], Coroutine[None, None, int]]], ...] = (
        (
            "page_views",
            lambda session: rows_deleted(
                session, sa_delete(WebPageView).where(WebPageView.created_at < analytics_cutoff)
            ),
        ),
        (
            "flow_events",
            lambda session: rows_deleted(
                session, sa_delete(WebFlowEvent).where(WebFlowEvent.created_at < analytics_cutoff)
            ),
        ),
        (
            "error_reports",
            lambda session: rows_deleted(
                session,
                sa_delete(WebErrorReport).where(
                    WebErrorReport.resolved.is_(True),
                    WebErrorReport.last_seen_at < error_cutoff,
                ),
            ),
        ),
        (
            "traffic_history",
            lambda session: rows_deleted(
                session, sa_delete(KeyTrafficHistory).where(KeyTrafficHistory.snapshot_date < traffic_cutoff)
            ),
        ),
        (
            "rate_limit",
            lambda session: rows_deleted(
                session,
                _sa_text("DELETE FROM rate_limit_counters WHERE window_start < :c"),
                {"c": rate_limit_cutoff},
            ),
        ),
        (
            "audit_events",
            lambda session: delete_old_audit_events(session, older_than_days=AUDIT_RETENTION_DAYS),
        ),
    )

    removed: list[str] = []
    for name, run in steps:
        async with async_session_maker() as session:
            try:
                count = await run(session)
                await session.commit()
                removed.append(f"{name}={count}")
            except Exception as error:
                await session.rollback()
                removed.append(f"{name}=ошибка")
                logger.error("[WebAnalyticsCleanup] шаг {} не выполнен: {}", name, error)
    logger.info("[WebAnalyticsCleanup] удалено {}", " ".join(removed))


async def abandoned_checkout_reminder_job() -> None:
    """Напоминает пользователям о незавершённой оплате (брошенный checkout)."""
    from services.abandoned_checkout import send_abandoned_checkout_reminders

    async with async_session_maker() as session:
        try:
            count = await send_abandoned_checkout_reminders(session)
            await session.commit()
            if count:
                logger.info("[AbandonedCheckout] Напоминаний отправлено: {}", count)
        except Exception as error:
            logger.error("[AbandonedCheckout] Ошибка отправки напоминаний: {}", error)



async def import_guard_job() -> None:
    """Повторная проверка пути импорта.

    Одной проверки при старте мало: ленивые роутеры подтягивают пакеты по
    первому запросу, то есть много позже, и утечка появляется уже на ходу.
    """
    from utils.import_guard import warn_on_foreign_modules

    warn_on_foreign_modules("проверка на ходу")

async def snapshot_key_traffic_job() -> None:
    """Дневной снапшот использованного трафика по ключам (для графиков использования)."""
    from services.traffic_history import snapshot_all_key_traffic

    async with async_session_maker() as session:
        try:
            count = await snapshot_all_key_traffic(session)
            await session.commit()
            if count:
                logger.info("[TrafficHistory] Снапшотов трафика записано: {}", count)
        except Exception as error:
            logger.error("[TrafficHistory] Ошибка снапшота трафика: {}", error)


async def snapshot_key_traffic_hourly_job() -> None:
    """Почасовой снапшот использованного трафика (для графика использования за сутки)."""
    from services.traffic_history import snapshot_all_key_traffic_hourly

    async with async_session_maker() as session:
        try:
            count = await snapshot_all_key_traffic_hourly(session)
            await session.commit()
            if count:
                logger.info("[TrafficHistory] Почасовых снапшотов записано: {}", count)
        except Exception as error:
            logger.error("[TrafficHistory] Ошибка почасового снапшота: {}", error)


async def snapshot_subscription_metrics_job() -> None:
    """Дневной снапшот подписок (активные/отток/тарифы) + одноразовый backfill из платежей."""
    from database.subscription_events import backfill_from_payments, snapshot_daily_metrics

    async with async_session_maker() as session:
        try:
            seeded = await backfill_from_payments(session)
            await snapshot_daily_metrics(session)
            await session.commit()
            if seeded:
                logger.info("[SubMetrics] backfill из платежей: записано {} событий", seeded)
        except Exception as error:
            logger.error("[SubMetrics] Ошибка снапшота подписок: {}", error)


async def anomaly_check_job() -> None:
    """Утренняя проверка аномалий: высокий процент отказов платежей и всплеск оттока."""
    from datetime import datetime, timedelta

    from sqlalchemy import func, select

    from database.models import Payment, SubscriptionEvent

    now = datetime.utcnow()
    y_start = datetime(now.year, now.month, now.day) - timedelta(days=1)
    y_end = y_start + timedelta(days=1)
    alerts: list[str] = []
    async with async_session_maker() as session:
        try:
            row = (
                await session.execute(
                    select(
                        func.count().filter(Payment.status == "success").label("ok"),
                        func.count().filter(Payment.status.in_(["failed", "cancelled"])).label("bad"),
                    )
                    .where(Payment.created_at >= y_start)
                    .where(Payment.created_at < y_end)
                )
            ).first()
            ok = int(row.ok or 0) if row else 0
            bad = int(row.bad or 0) if row else 0
            total = ok + bad
            if total >= 10 and bad / total > 0.3:
                alerts.append(
                    f"⚠️ Высокий процент отказов платежей за вчера: {bad}/{total} ({round(bad / total * 100)}%)."
                )

            exp_y = (
                await session.scalar(
                    select(func.count())
                    .select_from(SubscriptionEvent)
                    .where(SubscriptionEvent.event_type.in_(["expired", "deleted"]))
                    .where(SubscriptionEvent.created_at >= y_start)
                    .where(SubscriptionEvent.created_at < y_end)
                )
            ) or 0
            week_start = y_start - timedelta(days=7)
            exp_week = (
                await session.scalar(
                    select(func.count())
                    .select_from(SubscriptionEvent)
                    .where(SubscriptionEvent.event_type.in_(["expired", "deleted"]))
                    .where(SubscriptionEvent.created_at >= week_start)
                    .where(SubscriptionEvent.created_at < y_start)
                )
            ) or 0
            avg = exp_week / 7.0
            if avg >= 5 and exp_y > avg * 2:
                alerts.append(f"⚠️ Всплеск оттока за вчера: истекло {exp_y} (среднее за неделю ~{round(avg)}).")
        except Exception as error:
            logger.error("[Anomaly] ошибка проверки аномалий: {}", error)
            return

    if alerts:
        try:
            from services.admin_alert import send_admin_alert

            await send_admin_alert("📊 Аналитика — внимание:\n" + "\n".join(alerts))
        except Exception as error:
            logger.error("[Anomaly] не удалось отправить алерт: {}", error)


async def log_db_pool_status() -> None:
    """Раз в минуту логирует состояние пула соединений: даёт видимость «упираемся ли в лимит»."""
    try:
        from database.db import engine

        pool = engine.pool
        size = pool.size()
        checked_out = pool.checkedout()
        checked_in = getattr(pool, "checkedin", lambda: size - checked_out)()
        raw_overflow = getattr(pool, "overflow", lambda: 0)()
        overflow_in_use = max(0, raw_overflow)
        logger.info(
            "[DBPool] пул={} занято={} свободно={} сверх лимита={}",
            size,
            checked_out,
            checked_in,
            overflow_in_use,
        )
    except Exception as error:
        logger.debug("[DBPool] не удалось получить статус пула: {}", error)


AUDIT_DRAIN_TRIGGER = CronTrigger(hour=0, minute=0, timezone="Europe/Moscow")
#: Проверка длины очереди — раз в десять минут, сам слив только при превышении.
AUDIT_DRAIN_BY_LENGTH_TRIGGER = IntervalTrigger(minutes=10)
DAILY_STATS_REPORT_TRIGGER = CronTrigger(hour=0, minute=1, timezone="Europe/Moscow")
MONTHLY_STATS_REPORT_TRIGGER = CronTrigger(day=1, hour=0, minute=10, timezone="Europe/Moscow")
STALE_PAYMENTS_SWEEP_TRIGGER = CronTrigger(minute=0, timezone="Europe/Moscow")
EXPIRED_GIFTS_CLEANUP_TRIGGER = CronTrigger(hour=3, minute=0, timezone="Europe/Moscow")
AUTOCLOSE_TICKETS_TRIGGER = CronTrigger(hour=4, minute=0, timezone="Europe/Moscow")
WEB_ANALYTICS_CLEANUP_TRIGGER = CronTrigger(hour=3, minute=30, timezone="Europe/Moscow")
ABANDONED_CHECKOUT_TRIGGER = CronTrigger(minute=20, timezone="Europe/Moscow")
KEY_TRAFFIC_SNAPSHOT_TRIGGER = CronTrigger(hour=0, minute=10, timezone="Europe/Moscow")
KEY_TRAFFIC_HOURLY_SNAPSHOT_TRIGGER = CronTrigger(minute=5, timezone="Europe/Moscow")
SUBSCRIPTION_METRICS_SNAPSHOT_TRIGGER = CronTrigger(hour=0, minute=20, timezone="Europe/Moscow")
ANOMALY_CHECK_TRIGGER = CronTrigger(hour=9, minute=0, timezone="Europe/Moscow")
DB_POOL_STATUS_TRIGGER = IntervalTrigger(minutes=1)
IMPORT_GUARD_TRIGGER = IntervalTrigger(hours=1)
