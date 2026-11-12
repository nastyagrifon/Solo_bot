#!/usr/bin/env python3
"""Гигиена обращений закрывает их, а не удаляет.

Прежняя `delete_stale` удаляла отвеченные и закрытые обращения вместе с
перепиской и темой форума: история клиента исчезала из кабинета за сутки.
Теперь `close_stale` только переводит в `closed`.

Запуск из корня проекта: ``venv/bin/python -m unittest discover -s tests -v``
"""

import asyncio
import importlib.util
import pathlib
import sys
import types
import unittest
from datetime import UTC, datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parent.parent


def zaglushit_sqlalchemy_esli_net():
    """На машине без ядра sqlalchemy может не стоять — тогда подменяем.

    В venv бота библиотека настоящая, и подмена не срабатывает: проверяем
    отбор на том же коде, что идёт в прод.
    """
    try:
        import sqlalchemy  # noqa: F401
        return
    except ImportError:
        pass

    sa = types.ModuleType("sqlalchemy")

    class _Q:
        def where(self, *a, **k):
            return self

        def order_by(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        def offset(self, *a, **k):
            return self

    sa.select = lambda *a, **k: _Q()
    sa.update = lambda *a, **k: _Q()
    sa.and_ = sa.or_ = lambda *a, **k: True
    sa.case = lambda *a, **k: None
    sa.cast = lambda *a, **k: None
    sa.func = types.SimpleNamespace(count=lambda *a: None, lower=lambda *a: None)
    sa.Text = object
    sys.modules["sqlalchemy"] = sa
    ext = types.ModuleType("sqlalchemy.ext")
    ext.__path__ = []
    asyncio_mod = types.ModuleType("sqlalchemy.ext.asyncio")
    asyncio_mod.AsyncSession = object
    sys.modules["sqlalchemy.ext"] = ext
    sys.modules["sqlalchemy.ext.asyncio"] = asyncio_mod


def load_service():
    """Грузим файлом с заглушками: пакет ядра тянет базу и настройки."""
    zaglushit_sqlalchemy_esli_net()
    models = types.ModuleType("database.models")

    class _Col:
        def __eq__(self, other):
            return ("eq", other)

        def __lt__(self, other):
            return ("lt", other)

    class _M:
        id = status = last_message_at = updated_at = priority = identity_id = _Col()
        created_at = ticket_id = author = _Col()

    models.Identity = models.Ticket = models.TicketMessage = _M
    database = types.ModuleType("database")
    database.__path__ = []
    sys.modules.setdefault("database", database)
    sys.modules["database.models"] = models
    users = types.ModuleType("database.users")

    async def get_billing_user_id_for_identity(*a, **k):
        return None

    users.get_billing_user_id_for_identity = get_billing_user_id_for_identity
    sys.modules["database.users"] = users

    spec = importlib.util.spec_from_file_location(
        "tickets_service_under_test", ROOT / "services" / "tickets" / "service.py")
    module = importlib.util.module_from_spec(spec)
    events = types.ModuleType("events_stub")
    sobytiya = []

    async def publish_tickets_changed(*a, **k):
        sobytiya.append("changed")

    events.publish_tickets_changed = publish_tickets_changed
    sys.modules["tickets_service_under_test.events"] = events
    # Очередь доставки вендора сервису нужна только как набор имён.
    queue = types.ModuleType("queue_stub")

    async def _nichego(*a, **k):
        return None

    for imya in ("queue_assignment", "queue_message", "queue_new_ticket", "queue_topic_delete", "queue_topic_state"):
        setattr(queue, imya, _nichego)
    sys.modules["tickets_service_under_test.queue"] = queue
    # относительный импорт `.events` ищется в пакете, поэтому подменяем его
    module.__package__ = "tickets_service_under_test"
    sys.modules["tickets_service_under_test"] = module
    spec.loader.exec_module(module)
    return module, sobytiya


class FakeTicket:
    def __init__(self, tid, status, last):
        self.identity_id = None  # сервис вендора шлёт событие по владельцу обращения
        self.id = tid
        self.status = status
        self.last_message_at = last
        self.updated_at = None


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.flushes = 0
        self.udalennye = []

    async def execute(self, _q):
        return FakeResult(self.rows)

    async def flush(self):
        self.flushes += 1

    async def delete(self, obj):
        self.udalennye.append(obj)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class CloseStale(unittest.TestCase):
    def setUp(self):
        self.service, self.sobytiya = load_service()

    def test_stavit_closed_i_ne_udalyaet(self):
        staryy = datetime.now(UTC) - timedelta(days=5)
        rows = [FakeTicket("t1", "answered", staryy), FakeTicket("t2", "answered", staryy)]
        session = FakeSession(rows)
        skolko = run(self.service.close_stale(session, answered_days=2))
        self.assertEqual(skolko, 2)
        self.assertEqual([t.status for t in rows], ["closed", "closed"])
        self.assertEqual(session.udalennye, [], "обращения удалять нельзя")
        self.assertTrue(all(t.updated_at is not None for t in rows))
        # Событие теперь адресное, по владельцу обращения: одно на каждое закрытое.
        self.assertEqual(self.sobytiya, ["changed"] * len(rows))

    def test_pustoy_otbor_nichego_ne_delaet(self):
        session = FakeSession([])
        self.assertEqual(run(self.service.close_stale(session)), 0)
        self.assertEqual(session.flushes, 0)
        self.assertEqual(self.sobytiya, [])

    def test_temu_foruma_ne_trogaem(self):
        """Удаление темы в гигиене недопустимо: она живёт на клиента."""
        text = (ROOT / "services" / "tickets" / "service.py").read_text(encoding="utf-8")
        telo = text[text.index("async def close_stale("):text.index("async def resolve_billing_user_ref(")]
        self.assertNotIn("delete_topic", telo)
        self.assertNotIn("session.delete", telo)

    def test_cron_zovet_zakrytie(self):
        text = (ROOT / "core" / "tasks" / "cron_tasks.py").read_text(encoding="utf-8")
        self.assertIn("close_stale", text)
        self.assertNotIn("delete_stale", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
