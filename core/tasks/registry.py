from core.tasks.cron_tasks import (
    ABANDONED_CHECKOUT_TRIGGER,
    ANOMALY_CHECK_TRIGGER,
    AUDIT_DRAIN_TRIGGER,
    AUTOCLOSE_TICKETS_TRIGGER,
    DAILY_STATS_REPORT_TRIGGER,
    DB_POOL_STATUS_TRIGGER,
    EXPIRED_GIFTS_CLEANUP_TRIGGER,
    KEY_TRAFFIC_HOURLY_SNAPSHOT_TRIGGER,
    KEY_TRAFFIC_SNAPSHOT_TRIGGER,
    MONTHLY_STATS_REPORT_TRIGGER,
    STALE_PAYMENTS_SWEEP_TRIGGER,
    SUBSCRIPTION_METRICS_SNAPSHOT_TRIGGER,
    WEB_ANALYTICS_CLEANUP_TRIGGER,
    abandoned_checkout_reminder_job,
    anomaly_check_job,
    autoclose_stale_tickets_job,
    cleanup_expired_gifts_job,
    cleanup_web_analytics_job,
    log_db_pool_status,
    run_in_own_loop,
    scheduled_audit_drain,
    scheduled_monthly_stats_report,
    scheduled_stats_report,
    snapshot_key_traffic_hourly_job,
    snapshot_key_traffic_job,
    snapshot_subscription_metrics_job,
    sweep_stale_payments_job,
)
from core.tasks.loop_tasks import (
    backup_loop,
    backup_thread_loop,
    blocked_drain_loop,
    notifications_loop,
    remnawave_monitor_loop,
    scheduled_broadcasts_loop_task,
    server_checks_loop,
)
from core.slow_log import loop_lag_loop
from core.tasks.periodic_manager import periodic_task_manager


# (id, runner, может ли жить в своём процессе). Порядок = порядок регистрации:
# процессы раздаются первым по списку, пока хватает PROCESS_POOL_SIZE.
LOOP_TASKS = (
    ("notifications", notifications_loop, True),
    ("scheduled_broadcasts", scheduled_broadcasts_loop_task, True),
    ("backup", backup_loop, True),
    ("blocked_drain", blocked_drain_loop, False),
    ("server_checks", server_checks_loop, True),
    ("remnawave_monitor", remnawave_monitor_loop, False),
    ("loop_lag", loop_lag_loop, False),
)

# (id, async job, trigger, можно ли в processpool). Остаток бюджета после петель
# либо >0 — тогда все process_ok идут в processpool, либо 0 — все в event loop.
CRON_TASKS = (
    ("audit_drain_midnight", scheduled_audit_drain, AUDIT_DRAIN_TRIGGER, True),
    ("daily_stats_report", scheduled_stats_report, DAILY_STATS_REPORT_TRIGGER, False),
    ("monthly_stats_report", scheduled_monthly_stats_report, MONTHLY_STATS_REPORT_TRIGGER, False),
    ("sweep_stale_payments", sweep_stale_payments_job, STALE_PAYMENTS_SWEEP_TRIGGER, True),
    ("cleanup_expired_gifts", cleanup_expired_gifts_job, EXPIRED_GIFTS_CLEANUP_TRIGGER, True),
    ("autoclose_stale_tickets", autoclose_stale_tickets_job, AUTOCLOSE_TICKETS_TRIGGER, True),
    ("cleanup_web_analytics", cleanup_web_analytics_job, WEB_ANALYTICS_CLEANUP_TRIGGER, True),
    ("abandoned_checkout_reminder", abandoned_checkout_reminder_job, ABANDONED_CHECKOUT_TRIGGER, True),
    ("snapshot_key_traffic", snapshot_key_traffic_job, KEY_TRAFFIC_SNAPSHOT_TRIGGER, True),
    ("snapshot_key_traffic_hourly", snapshot_key_traffic_hourly_job, KEY_TRAFFIC_HOURLY_SNAPSHOT_TRIGGER, True),
    ("snapshot_subscription_metrics", snapshot_subscription_metrics_job, SUBSCRIPTION_METRICS_SNAPSHOT_TRIGGER, True),
    ("anomaly_check", anomaly_check_job, ANOMALY_CHECK_TRIGGER, True),
    ("db_pool_status", log_db_pool_status, DB_POOL_STATUS_TRIGGER, False),
)


_TASKS_REGISTERED = False


def register_periodic_tasks() -> None:
    global _TASKS_REGISTERED
    if _TASKS_REGISTERED:
        return
    from settings.config import PROCESS_POOL_SIZE

    process_budget = max(0, int(PROCESS_POOL_SIZE) if int(PROCESS_POOL_SIZE) > 1 else 0)
    manager = periodic_task_manager

    for task_id, runner, process_ok in LOOP_TASKS:
        if process_ok and process_budget > 0:
            manager.register_process_loop_task(task_id, runner)
            process_budget -= 1
        elif runner is backup_loop:
            manager.register_thread_loop_task(task_id, backup_thread_loop)
        else:
            manager.register_loop_task(task_id, runner)

    manager.set_scheduler_process_workers(process_budget)

    for task_id, job, trigger, process_ok in CRON_TASKS:
        if process_ok and process_budget > 0:
            # Не partial: APScheduler не сериализует Job с partial, поэтому job едет в args.
            manager.register_cron_task(task_id, run_in_own_loop, trigger, execution_mode="process", args=(job,))
        else:
            manager.register_cron_task(task_id, job, trigger)

    _TASKS_REGISTERED = True
