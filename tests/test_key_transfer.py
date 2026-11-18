"""Перенос подписки на другой профиль бота.

SQLAlchemy подменён записывающей заглушкой: проверяем, КАКИЕ строки уходят
новому владельцу (только этого ключа, платежи не трогаются), что автоплатёж
с карты прежнего владельца снимается, что отказ до транзакции ничего не
меняет и что сбой панели после коммита не откатывает базу, а возвращается.
Запуск из корня проекта: ``venv/bin/python -m unittest discover -s tests -v``
"""

import asyncio
import contextlib
import importlib.util
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


class Col:
    def __init__(self, table, name):
        self.table, self.name = table, name

    def __eq__(self, other):
        return ("eq", self.table, self.name, other)

    __hash__ = object.__hash__

    def is_(self, other):
        return ("is", self.table, self.name, other)

    def startswith(self, prefix, autoescape=False):
        return ("startswith", self.table, self.name, prefix)


def _model(table, *cols):
    return type(table, (), {"__tablename__": table, **{c: Col(table, c) for c in cols}})


class Stmt:
    def __init__(self, kind, target):
        self.kind, self.target, self.conds, self.vals = kind, target, [], {}

    def where(self, *conds):
        self.conds += conds
        return self

    def values(self, **vals):
        self.vals.update(vals)
        return self

    def with_for_update(self):
        return self

    @property
    def table(self):
        target = self.target
        return getattr(target, "__tablename__", None) or target.table


MODELS = {
    "Key": _model("keys", "user_id", "client_id", "tg_id"),
    "KeyTrafficHistory": _model("key_traffic_history", "client_id", "tg_id"),
    "KeyTrafficHourly": _model("key_traffic_hourly", "client_id", "tg_id"),
    "Notification": _model("notifications", "user_id", "notification_type", "tg_id"),
    "SubscriptionEvent": _model("subscription_events", "user_id", "client_id", "tg_id"),
    "User": _model("users", "id"),
    "YooKassaSubscription": _model("yookassa_autopay_subscriptions", "id", "user_id", "client_id", "is_active"),
}


class World:
    """Всё, что операция сделала, по порядку."""

    def __init__(self):
        self.log, self.locked, self.dst, self.autopay_ids = [], True, None, [7]
        self.panel = None


W = World()


class FakeSession:
    async def execute(self, stmt):
        W.log.append(("exec", stmt))

    async def scalar(self, stmt):
        W.log.append(("scalar", stmt))
        return W.dst

    async def scalars(self, stmt):
        W.log.append(("scalars", stmt))
        return types.SimpleNamespace(all=lambda: list(W.autopay_ids))


@contextlib.asynccontextmanager
async def operation_session(session):
    W.log.append(("begin",))
    try:
        yield FakeSession()
    except BaseException:
        W.log.append(("rollback",))
        raise
    W.log.append(("commit",))


SAVED = {}


def _stub(name, **attrs):
    SAVED.setdefault(name, sys.modules.get(name))
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


async def _rec(name, *a, **k):
    W.log.append((name, a, k))


async def lock_owned_key_for_operation(session, user_id, client_id, email):
    W.log.append(("lock", int(user_id), client_id, email))
    return Key() if W.locked else None


async def panel_identity_fields(session, ref):
    return W.dst_panel


async def with_remnawave_api(session, server_ref, op, **kwargs):
    W.log.append(("panel", server_ref))
    if W.panel is None:
        return None
    return await op(W.panel)


def _load():
    _stub("sqlalchemy", select=lambda t: Stmt("select", t), update=lambda t: Stmt("update", t),
          delete=lambda t: Stmt("delete", t))
    _stub("audit", safe_record_audit_event=lambda session, **k: _rec("audit", **k))
    _stub("database")
    _stub("database.access")
    _stub("database.access.resolution", UserId=int, panel_identity_fields=panel_identity_fields)
    _stub("database.keys", lock_owned_key_for_operation=lock_owned_key_for_operation,
          invalidate_key_details=lambda *a: _rec("inv_details", *a),
          invalidate_key_email=lambda *a: _rec("inv_email", *a),
          invalidate_keys_list=lambda s, ref: _rec("inv_list", int(ref)))
    _stub("database.models", **MODELS)
    _stub("database.yookassa_autopay", cancel_subscription=lambda s, uid, sid: _rec("cancel_autopay", uid, sid))
    log = types.SimpleNamespace(info=lambda *a: None, error=lambda *a: W.log.append(("error", a)))
    _stub("logger", logger=log, PANEL_REMNA="[Remnawave]")
    _stub("middlewares")
    _stub("middlewares.session", operation_session=operation_session)
    _stub("panels")
    _stub("panels.remnawave_runtime", with_remnawave_api=with_remnawave_api,
          invalidate_remnawave_profile=lambda *a, **k: _rec("inv_profile", *a))
    spec = importlib.util.spec_from_file_location("key_transfer", ROOT / "services/operations/key_transfer.py")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        # Заглушки нужны только на время загрузки: другим тестам в том же процессе нужны настоящие модули.
        for name, original in SAVED.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
    return module


kt = _load()


class Key:
    user_id, client_id, email, server_id = 1, "cid-1", "mail@x", "srv-1"
    remnawave_link, key, tg_id = "https://s/1", None, 11


class FakeResponse:
    def __init__(self, body):
        self.status_code, self._body = 200, body

    def json(self):
        return {"response": self._body}


class FakePanel:
    def __init__(self, echo=True):
        self.echo, self.patches = echo, []

    async def get_user_by_uuid(self, uuid, username=None):
        return {"uuid": "u-1", "telegramId": 11, "email": "old@x"}

    async def _request(self, method, path, json=None):
        self.patches.append(json)
        body = {"uuid": "u-1", "telegramId": 11, "email": "old@x"}
        if self.echo:
            body.update({k: v for k, v in json.items() if k != "uuid"})
        return FakeResponse(body)


def run(coro):
    return asyncio.run(coro)


class TransferTest(unittest.TestCase):
    def setUp(self):
        W.__init__()
        W.dst = types.SimpleNamespace(id=5, tg_id=55)
        W.dst_panel = (55, None)

    def _writes(self):
        return [e[1] for e in W.log if e[0] == "exec"]

    def _write(self, table, kind="update"):
        found = [s for s in self._writes() if s.table == table and s.kind == kind]
        self.assertEqual(len(found), 1, f"{kind} {table}: {found}")
        return found[0]

    def test_key_moves_to_new_owner(self):
        run(kt.transfer_key(None, Key(), 5, actor_tg_id=900))
        key = self._write("keys")
        self.assertEqual(key.vals, {"user_id": 5, "tg_id": 55})
        self.assertIn(("eq", "keys", "user_id", 1), key.conds)
        self.assertIn(("eq", "keys", "client_id", "cid-1"), key.conds)

    def test_only_rows_of_this_key(self):
        run(kt.transfer_key(None, Key(), 5, actor_tg_id=900))
        events = self._write("subscription_events")
        self.assertIn(("eq", "subscription_events", "client_id", "cid-1"), events.conds)
        self.assertIn(("eq", "subscription_events", "user_id", 1), events.conds)
        for table in ("key_traffic_history", "key_traffic_hourly"):
            self.assertIn(("eq", table, "client_id", "cid-1"), self._write(table).conds)
        notes = self._write("notifications")
        self.assertIn(("startswith", "notifications", "notification_type", "mail@x_"), notes.conds)
        self.assertEqual(notes.vals, {"user_id": 5, "tg_id": 55})

    def test_payments_stay_with_payer(self):
        run(kt.transfer_key(None, Key(), 5, actor_tg_id=900))
        tables = {s.table for s in self._writes()}
        self.assertNotIn("payments", tables)
        self.assertNotIn("yookassa_autopay_subscriptions", tables)
        self.assertIn(("cancel_autopay", (1, 7), {}), W.log)

    def test_audit_inside_transaction(self):
        run(kt.transfer_key(None, Key(), 5, actor_tg_id=900))
        names = [e[0] for e in W.log]
        audit = next(e for e in W.log if e[0] == "audit")
        self.assertLess(names.index("audit"), names.index("commit"))
        self.assertEqual(audit[2]["actor_tg_id"], 900)
        self.assertEqual(audit[2]["entity_id"], "cid-1")
        self.assertEqual(audit[2]["metadata"]["to_user_id"], 5)

    def test_both_owners_caches_dropped(self):
        run(kt.transfer_key(None, Key(), 5, actor_tg_id=900))
        self.assertIn(("inv_list", (1,), {}), W.log)
        self.assertIn(("inv_list", (5,), {}), W.log)

    def test_stale_key_changes_nothing(self):
        W.locked = False
        with self.assertRaises(kt.KeyTransferError):
            run(kt.transfer_key(None, Key(), 5, actor_tg_id=900))
        self.assertEqual(self._writes(), [])
        self.assertIn(("rollback",), W.log)
        self.assertNotIn(("panel", "srv-1"), W.log)

    def test_same_owner_refused(self):
        with self.assertRaises(kt.KeyTransferError):
            run(kt.transfer_key(None, Key(), 1, actor_tg_id=900))
        self.assertEqual(W.log, [])

    def test_missing_target_changes_nothing(self):
        W.dst = None
        with self.assertRaises(kt.KeyTransferError):
            run(kt.transfer_key(None, Key(), 5, actor_tg_id=900))
        self.assertEqual(self._writes(), [])

    def test_panel_failure_after_commit_is_reported_not_rolled_back(self):
        W.panel = None
        self.assertFalse(run(kt.transfer_key(None, Key(), 5, actor_tg_id=900)))
        names = [e[0] for e in W.log]
        self.assertLess(names.index("commit"), names.index("panel"))
        self.assertNotIn("rollback", names)
        self.assertIn("error", names)

    def test_panel_gets_new_owner_and_old_email_cleared(self):
        W.panel = FakePanel()
        self.assertTrue(run(kt.transfer_key(None, Key(), 5, actor_tg_id=900)))
        self.assertEqual(W.panel.patches, [{"uuid": "u-1", "telegramId": 55, "email": None}])

    def test_panel_ignoring_patch_is_failure(self):
        W.panel = FakePanel(echo=False)
        self.assertFalse(run(kt.transfer_key(None, Key(), 5, actor_tg_id=900)))


if __name__ == "__main__":
    unittest.main()
