"""Импорт и экспорт банов, бан по tg id без клиента.

Бан один — строка manual_bans. Импорт только дополняет: существующий бан не
меняется. Бан по tg id того, кого ещё нет в базе, — та же таблица с пустым
user_id: проверка банов находит его по tg id апдейта до регистрации.
Ядро подменено заглушками; SQL-выражения разбираются маленьким вычислителем
над строками в памяти.
Запуск из корня проекта: ``venv/bin/python -m unittest tests.test_ban_import_export -v``
"""

import asyncio
import types
import unittest
from datetime import datetime, timedelta, timezone

from tests.test_ban_public_reason import ROOT, CallbackQuery, Message, _exec, _module

INTERNAL = "служебная пометка"
PUBLIC = "нарушение правил"


def run(coro):
    return asyncio.run(coro)


# --- Вычислитель выражений ----------------------------------------------------------------------


class E:
    """Выражение-заглушка: строит дерево, которое FakeDb вычисляет на строке."""

    def __init__(self, *node):
        self.node = node

    def __eq__(self, other):
        return E("eq", self, other)

    def __ne__(self, other):
        return E("ne", self, other)

    def __gt__(self, other):
        return E("gt", self, other)

    def __and__(self, other):
        return E("and", self, other)

    def __or__(self, other):
        return E("or", self, other)

    __hash__ = object.__hash__

    def is_(self, other):
        return E("eq", self, other)

    def in_(self, values):
        return E("in", self, list(values))

    def label(self, name):
        return E("label", self, name)

    def nulls_last(self):
        return self

    def isnot(self, other):
        return E("ne", self, other)


def col(name):
    return E("col", name)


NOW = E("now")


def ev(expr, row):
    if not isinstance(expr, E):
        return expr
    op, *args = expr.node
    if op == "col":
        return row.get(args[0])
    if op == "now":
        return datetime.now(timezone.utc)
    if op == "label":
        return ev(args[0], row)
    if op == "case":
        cond, then, other = args
        return ev(then, row) if ev(cond, row) else ev(other, row)
    a, b = ev(args[0], row), ev(args[1], row) if len(args) > 1 else None
    if op == "eq":
        return a == b
    if op == "ne":
        return a != b
    if op == "gt":
        return a is not None and a > b
    if op == "and":
        return bool(a) and bool(b)
    if op == "or":
        return any(ev(x, row) for x in args)
    if op == "in":
        return a in b
    raise AssertionError(op)


def or_(*args):
    return E("or", *args)


def case(whens, else_):
    cond, then = whens
    return E("case", cond, then, else_)


def _name(expr):
    op, *args = expr.node
    if op == "label":
        return args[1]
    if op == "col":
        return args[0].split(".")[1]
    return "value"


class Select:
    def __init__(self, *cols):
        self.cols, self.conds = cols, []

    def where(self, *conds):
        self.conds.extend(conds)
        return self

    def select_from(self, *a):
        return self

    def outerjoin(self, *a):
        return self

    join = outerjoin

    def limit(self, *a):
        return self

    def order_by(self, *a):
        return self


class Delete:
    def __init__(self, model):
        self.conds = []

    def where(self, *conds):
        self.conds.extend(conds)
        return self


class Insert:
    def __init__(self, model):
        self.rows, self.conflict = [], None

    def values(self, rows=None, **kw):
        self.rows = rows if isinstance(rows, list) else [kw]
        return self

    def on_conflict_do_nothing(self, **kw):
        self.conflict = "nothing"
        return self

    def on_conflict_do_update(self, index_elements=None, set_=None, **kw):
        self.conflict = ("update", set_)
        return self


class Result:
    def __init__(self, rows=(), rowcount=None):
        self.rows, self.rowcount = list(rows), rowcount

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None

    def scalars(self):
        return Result([r[0] for r in self.rows])


BAN_COLS = ("user_id", "tg_id", "reason", "public_reason", "until", "banned_by", "banned_at")


class FakeDb:
    """manual_bans и users в памяти с теми же уникальностями, что в базе."""

    def __init__(self, users=None, bans=None):
        self.users = dict(users or {})  # id -> tg_id
        self.bans = [dict(dict.fromkeys(BAN_COLS), **b) for b in (bans or [])]
        self.queries = 0
        self.writes = 0

    def _ban_rows(self):
        for ban in self.bans:
            row = {f"ManualBan.{k}": v for k, v in ban.items()}
            row["User.tg_id"] = self.users.get(ban["user_id"])
            yield ban, row

    def _match(self, conds, row):
        return all(ev(c, row) for c in conds)

    def _conflicts(self, new):
        for ban in self.bans:
            if new["user_id"] is not None and ban["user_id"] == new["user_id"]:
                return ban
            if new["user_id"] is None and ban["user_id"] is None and ban["tg_id"] == new["tg_id"]:
                return ban
        return None

    async def execute(self, stmt):
        self.queries += 1
        if isinstance(stmt, Select):
            first = stmt.cols[0]
            if first.node[0] == "col" and first.node[1].startswith("User."):
                rows = [{"User.id": uid, "User.tg_id": tg} for uid, tg in self.users.items()]
            else:
                rows = [row for _, row in self._ban_rows()]
            out = []
            for row in rows:
                if self._match(stmt.conds, row):
                    values = tuple(ev(c, row) for c in stmt.cols)
                    named = types.SimpleNamespace(**{_name(c): v for c, v in zip(stmt.cols, values)})
                    out.append(_Row(values, named))
            return Result(out)
        self.writes += 1
        if isinstance(stmt, Delete):
            gone = [ban for ban, row in self._ban_rows() if self._match(stmt.conds, row)]
            self.bans = [b for b in self.bans if b not in gone]
            return Result(rowcount=len(gone))
        if isinstance(stmt, Insert):
            count = 0
            for new in stmt.rows:
                new = dict(dict.fromkeys(BAN_COLS), **new)
                old = self._conflicts(new)
                if old is None:
                    self.bans.append(new)
                    count += 1
                elif stmt.conflict != "nothing":
                    old.update(stmt.conflict[1])
                    count += 1
            return Result(rowcount=count)
        raise AssertionError(stmt)


class _Row(tuple):
    def __new__(cls, values, named):
        obj = super().__new__(cls, values)
        obj.__dict__.update(vars(named))
        return obj


ManualBan = types.SimpleNamespace(**{k: col(f"ManualBan.{k}") for k in BAN_COLS + ("id",)})
User = types.SimpleNamespace(id=col("User.id"), tg_id=col("User.tg_id"))
sa_func = types.SimpleNamespace(now=lambda: NOW, count=lambda: E("count"))


# --- Загрузка модулей ---------------------------------------------------------------------------


class _Observer:
    def __call__(self, *a, **k):
        return lambda fn: fn

    def filter(self, *a, **k):
        return None


class Router:
    def __init__(self):
        self.callback_query = _Observer()
        self.message = _Observer()


class Builder:
    def button(self, *a, **k):
        return self

    def row(self, *a, **k):
        return self

    def adjust(self, *a):
        return self

    def as_markup(self):
        return None


class AdminPanelCallback:
    def __init__(self, **kw):
        self.kw = kw

    def pack(self):
        return str(self.kw)

    @staticmethod
    def filter(*a, **k):
        return None


class BufferedInputFile:
    def __init__(self, file, filename):
        self.file, self.filename = file, filename


def _load_handler(invalidated):
    async def invalidate_ban_cache(tg_id):
        invalidated.append(tg_id)

    async def noop(*a, **k):
        return None

    text = lambda *p, **k: "\n".join(str(x) for x in p)  # noqa: E731
    stubs = {
        "aiogram": _module("aiogram", F=types.SimpleNamespace(action=E("col", "F.action")), Router=Router),
        "aiogram.fsm": _module("aiogram.fsm"),
        "aiogram.fsm.context": _module("aiogram.fsm.context", FSMContext=object),
        "aiogram.fsm.state": _module("aiogram.fsm.state", State=object, StatesGroup=object),
        "aiogram.types": _module(
            "aiogram.types", BufferedInputFile=BufferedInputFile, CallbackQuery=object, Message=object
        ),
        "aiogram.utils": _module("aiogram.utils"),
        "aiogram.utils.keyboard": _module("aiogram.utils.keyboard", InlineKeyboardBuilder=Builder),
        "sqlalchemy": _module("sqlalchemy", case=case, delete=Delete, func=sa_func, or_=or_, select=Select),
        "sqlalchemy.dialects": _module("sqlalchemy.dialects"),
        "sqlalchemy.dialects.postgresql": _module("sqlalchemy.dialects.postgresql", insert=Insert),
        "sqlalchemy.ext": _module("sqlalchemy.ext"),
        "sqlalchemy.ext.asyncio": _module("sqlalchemy.ext.asyncio", AsyncSession=object),
        "database": _module("database", delete_user_data=noop),
        "database.access": _module("database.access"),
        "database.access.resolution": _module(
            "database.access.resolution", TelegramId=int, UserId=int, resolve_user_optional=noop
        ),
        "database.models": _module("database.models", BlockedUser=None, Key=None, ManualBan=ManualBan, User=User),
        "database.users": _module("database.users", add_user=noop),
        "filters": _module("filters"),
        "filters.admin": _module("filters.admin", HasPermission=lambda *a: None, IsAdminFilter=lambda: None),
        "filters.permissions": _module("filters.permissions", PERM_MANAGEMENT="management"),
        "logger": _module("logger", logger=types.SimpleNamespace(info=noop_sync, error=noop_sync)),
        "middlewares": _module("middlewares"),
        "middlewares.ban_checker": _module("middlewares.ban_checker", invalidate_ban_cache=invalidate_ban_cache),
        "handlers": _module("handlers"),
        "handlers.admin": _module("handlers.admin"),
        "handlers.admin.panel": _module("handlers.admin.panel"),
        "handlers.admin.panel.headers": _module(
            "handlers.admin.panel.headers", menu_text=text, quote=text, section=text, wrap_text=text
        ),
        "handlers.admin.panel.keyboard": _module("handlers.admin.panel.keyboard", AdminPanelCallback=AdminPanelCallback),
        "handlers.admin.bans": _module("handlers.admin.bans"),
        "handlers.admin.bans.keyboard": _module(
            "handlers.admin.bans.keyboard",
            build_bans_kb=lambda: None,
            build_blocked_users_kb=lambda: None,
            build_manual_bans_kb=lambda: None,
            build_shadow_bans_kb=lambda: None,
        ),
    }
    stubs["handlers.admin.bans"].__path__ = [str(ROOT / "handlers/admin/bans")]
    return _exec("handlers.admin.bans.bans_handler", ROOT / "handlers/admin/bans/bans_handler.py", stubs)


def noop_sync(*a, **k):
    return None


def _load_checker(cache, owner_uid):
    async def cache_get(key):
        return cache.get(key)

    async def cache_set(key, value, ttl):
        cache[key] = value

    async def cache_delete(key):
        cache.pop(key, None)

    async def resolve_uid_cached(session, tg_id):
        return owner_uid

    stubs = {
        "pytz": _module("pytz", timezone=lambda name: timezone(timedelta(hours=3))),
        "aiogram": _module("aiogram", BaseMiddleware=object),
        "aiogram.types": _module(
            "aiogram.types", CallbackQuery=CallbackQuery, Message=Message, TelegramObject=object, Update=type("U", (), {})
        ),
        "sqlalchemy": _module("sqlalchemy", select=Select),
        "sqlalchemy.ext": _module("sqlalchemy.ext"),
        "sqlalchemy.ext.asyncio": _module("sqlalchemy.ext.asyncio", AsyncSession=object),
        "core": _module("core"),
        "core.redis_cache": _module(
            "core.redis_cache",
            cache_get=cache_get,
            cache_set=cache_set,
            cache_delete=cache_delete,
            cache_key=lambda *p: ":".join(map(str, p)),
        ),
        "database": _module("database", async_session_maker=None),
        "database.access": _module("database.access"),
        "database.access.resolution": _module(
            "database.access.resolution", TelegramId=int, resolve_uid_cached=resolve_uid_cached
        ),
        "database.models": _module("database.models", ManualBan=ManualBan),
        "logger": _module("logger", logger=types.SimpleNamespace(info=noop_sync, warning=noop_sync, debug=noop_sync)),
        "settings": _module("settings"),
        "settings.cache_config": _module("settings.cache_config", BAN_CACHE_TTL_SEC=60),
        "settings.config": _module("settings.config", ADMIN_ID=[999], SUPPORT_CHAT_URL="SUPPORT"),
    }
    return _exec("_ban_checker_tg_under_test", ROOT / "middlewares/ban_checker.py", stubs)


# --- Тесты --------------------------------------------------------------------------------------


class ParseFormats(unittest.TestCase):
    def setUp(self):
        self.mod = _load_handler([])

    def test_text_format(self):
        content = "# список\n\n123456 #спам в чате\n234567\nabc #плохо\n123456 #повтор\n  345678   #  \n-5\n"
        bans, bad = self.mod.parse_bans_file(content)
        self.assertEqual(list(bans), [123456, 234567, 345678])
        self.assertEqual(bans[123456], {"reason": "спам в чате", "public_reason": None, "until": None})
        self.assertIsNone(bans[234567]["reason"])
        self.assertIsNone(bans[345678]["reason"])
        self.assertEqual(bad, [5, 8])

    def test_own_csv_export(self):
        future = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0)
        past = datetime.now(timezone.utc) - timedelta(days=3)
        content = (
            "tg_id,banned_at,reason,public_reason,until,banned_by,user_id\r\n"
            f"4567,2026-01-01 00:00:00+00:00,{INTERNAL},{PUBLIC},,77,1\r\n"
            f"5678,2026-01-01 00:00:00+00:00,,,{future},77,\r\n"
            f"6789,2026-01-01 00:00:00+00:00,x,,{past},77,\r\n"
            ",2026-01-01 00:00:00+00:00,x,,,77,3\r\n"
            "7890,2026-01-01,x,,не дата,77,\r\n"
            "4567,2026-01-01,повтор,,,77,\r\n"
        )
        bans, bad = self.mod.parse_bans_file(content)
        self.assertEqual(list(bans), [4567, 5678])
        self.assertEqual(bans[4567], {"reason": INTERNAL, "public_reason": PUBLIC, "until": None})
        self.assertEqual(bans[5678]["until"], future)
        self.assertEqual(bad, [4, 5, 6])


class ImportAddsOnly(unittest.TestCase):
    def setUp(self):
        self.invalidated = []
        self.mod = _load_handler(self.invalidated)

    def test_existing_bans_untouched_new_added(self):
        until = datetime.now(timezone.utc) + timedelta(days=5)
        db = FakeDb(
            users={1: 1111, 2: 2222},
            bans=[
                {"user_id": 1, "tg_id": 1111, "reason": "старая", "public_reason": "старая публ.", "until": until},
                {"user_id": None, "tg_id": 3333, "reason": "без клиента"},
            ],
        )
        before = [dict(b) for b in db.bans]
        bans, _ = self.mod.parse_bans_file("1111 #новая\n3333 #новая\n2222 #клиент\n4444 #новичок\n")
        added, already = run(self.mod.import_bans(db, bans, banned_by=77))
        self.assertEqual((added, already), (2, 2))
        self.assertEqual(db.bans[:2], before, "существующие баны не меняются")
        new = {b["tg_id"]: b for b in db.bans[2:]}
        self.assertEqual(new[2222]["user_id"], 2, "существующему клиенту — обычный бан на клиента")
        self.assertIsNone(new[4444]["user_id"], "нового человека — бан по tg id без клиента")
        self.assertEqual(new[4444]["reason"], "новичок")
        self.assertIsNone(new[4444]["public_reason"])
        self.assertIsNone(new[4444]["until"])
        self.assertEqual(sorted(self.invalidated), [2222, 4444])

    def test_registered_client_with_tg_only_ban_counts_as_existing(self):
        db = FakeDb(users={5: 5555}, bans=[{"user_id": None, "tg_id": 5555, "reason": "раньше"}])
        added, already = run(self.mod.import_bans(db, {5555: {"reason": "x", "public_reason": None, "until": None}}, 77))
        self.assertEqual((added, already), (0, 1))
        self.assertEqual(len(db.bans), 1)

    def test_import_handler_reports_and_limits_size(self):
        db = FakeDb(bans=[{"user_id": None, "tg_id": 1234, "reason": "был"}])
        msg = DocMessage(b"1234 #a\n2345 #b\nbad line\n")
        state = FakeState()
        run(self.mod.handle_manual_bans_import_file(msg, state, db))
        self.assertIn("Добавлено: 1", msg.sent[-1])
        self.assertIn("Уже было: 1", msg.sent[-1])
        self.assertIn("Пропущено строк: 1", msg.sent[-1])
        self.assertIn("Плохие строки: 3", msg.sent[-1])
        self.assertTrue(state.cleared)

        big = DocMessage(b"1", size=self.mod.IMPORT_MAX_BYTES + 1)
        run(self.mod.handle_manual_bans_import_file(big, FakeState(), FakeDb()))
        self.assertFalse(big.downloaded, "крупный файл не скачивается")
        self.assertIn("больше", big.sent[-1])


class ExportChangesNothing(unittest.TestCase):
    def test_export_is_read_only_and_reimports(self):
        mod = _load_handler([])
        past = datetime.now(timezone.utc) - timedelta(days=1)
        db = FakeDb(
            users={1: 1111},
            bans=[
                {"user_id": 1, "tg_id": 999999, "reason": INTERNAL, "public_reason": PUBLIC, "banned_by": 77},
                {"user_id": None, "tg_id": 3333, "reason": "без клиента", "banned_by": 77},
                {"user_id": None, "tg_id": 4444, "reason": "истёк", "until": past},
                {"user_id": None, "tg_id": 5555, "reason": "shadow"},
            ],
        )
        before = [dict(b) for b in db.bans]
        cb = ExportCallback()
        run(mod.handle_manual_bans_export(cb, db))
        self.assertEqual(db.bans, before)
        self.assertEqual(db.writes, 0)
        csv_text = cb.message.documents[0].file.decode()
        self.assertTrue(csv_text.startswith("tg_id,banned_at,reason,public_reason,until,banned_by,user_id"))
        self.assertIn("1111,", csv_text, "у бана клиента tg id клиента, а не зеркало")
        self.assertNotIn("999999", csv_text)
        self.assertIn("3333,", csv_text)
        self.assertNotIn("4444", csv_text, "только активные")
        self.assertNotIn("5555", csv_text, "теневые — в своей выгрузке")

        bans, bad = mod.parse_bans_file(csv_text)
        self.assertEqual(bad, [])
        self.assertEqual(bans[1111]["public_reason"], PUBLIC)
        fresh = FakeDb()
        self.assertEqual(run(mod.import_bans(fresh, bans, 77)), (2, 0))


class TgIdBanWithoutClient(unittest.TestCase):
    def _hit(self, db, owner_uid, tg_id=3333):
        cache, passed = {}, []

        async def handler(event, data):
            passed.append(event)

        mod = _load_checker(cache, owner_uid)
        msg = Message(tg_id)
        run(mod.BanCheckerMiddleware()(handler, msg, {"session": db}))
        return msg, passed, cache, mod

    def test_unknown_person_is_blocked_before_registration(self):
        db = FakeDb(bans=[{"user_id": None, "tg_id": 3333, "reason": INTERNAL}])
        msg, passed, cache, mod = self._hit(db, owner_uid=None)
        self.assertEqual(passed, [], "апдейт не доходит до регистрации и пробного ключа")
        self.assertEqual(len(msg.sent), 1)
        self.assertIn(mod.DEFAULT_PUBLIC_BAN_REASON, msg.sent[0])
        self.assertNotIn(INTERNAL, msg.sent[0])
        self.assertNotIn(INTERNAL, str(cache))
        self.assertEqual(db.queries, 1, "без клиента запрос по user_id не делается")

    def test_ban_holds_after_registration(self):
        db = FakeDb(users={7: 3333}, bans=[{"user_id": None, "tg_id": 3333, "reason": INTERNAL}])
        msg, passed, _, _ = self._hit(db, owner_uid=7)
        self.assertEqual(passed, [])
        self.assertEqual(len(msg.sent), 1)

    def test_other_people_pass(self):
        db = FakeDb(bans=[{"user_id": None, "tg_id": 3333}, {"user_id": 1, "tg_id": 4444}])
        msg, passed, _, _ = self._hit(db, owner_uid=None, tg_id=8888)
        self.assertEqual(passed, [msg])

    def test_expired_tg_ban_passes(self):
        db = FakeDb(bans=[{"user_id": None, "tg_id": 3333, "until": datetime.now(timezone.utc) - timedelta(hours=1)}])
        msg, passed, _, _ = self._hit(db, owner_uid=None)
        self.assertEqual(passed, [msg])

    def test_imported_text_reason_never_reaches_client(self):
        invalidated = []
        handler_mod = _load_handler(invalidated)
        db = FakeDb()
        bans, _ = handler_mod.parse_bans_file(f"3333 #{INTERNAL}\n")
        run(handler_mod.import_bans(db, bans, 77))
        msg, passed, cache, _ = self._hit(db, owner_uid=None)
        self.assertEqual(passed, [])
        self.assertNotIn(INTERNAL, msg.sent[0])
        self.assertNotIn(INTERNAL, str(cache))


class UnbanByTgId(unittest.TestCase):
    def test_unban_removes_tg_only_and_client_ban(self):
        invalidated = []
        mod = _load_handler(invalidated)
        db = FakeDb(
            users={1: 1111},
            bans=[
                {"user_id": None, "tg_id": 3333},
                {"user_id": 1, "tg_id": 1111},
                {"user_id": None, "tg_id": 4444},
            ],
        )
        msg = DocMessage(None, text="3333\n1111\nмусор")
        state = FakeState()
        run(mod.handle_manual_bans_unban_input(msg, state, db))
        self.assertEqual([b["tg_id"] for b in db.bans], [4444])
        self.assertEqual(set(invalidated), {3333, 1111})
        self.assertIn("2", msg.sent[-1])
        self.assertTrue(state.cleared)

    def test_unbanned_person_passes_checker(self):
        mod = _load_handler([])
        db = FakeDb(bans=[{"user_id": None, "tg_id": 3333}])
        run(mod.handle_manual_bans_unban_input(DocMessage(None, text="3333"), FakeState(), db))
        cache, passed = {}, []

        async def handler(event, data):
            passed.append(event)

        msg = Message(3333)
        run(_load_checker(cache, None).BanCheckerMiddleware()(handler, msg, {"session": db}))
        self.assertEqual(passed, [msg])


class SchemaAllowsBanWithoutClient(unittest.TestCase):
    def test_model_user_id_nullable_with_unique_indexes(self):
        src = (ROOT / "database/models/users.py").read_text(encoding="utf-8")
        body = src.split("class ManualBan", 1)[1].split("\nclass ", 1)[0]
        self.assertIn('user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)', body)
        self.assertIn('Index("uq_manual_bans_user_id", "user_id", unique=True)', body, "upsert по user_id держится")
        self.assertIn('postgresql_where=text("user_id IS NULL")', body)

    def test_migration_drops_not_null_and_is_registered(self):
        src = (ROOT / "database/migrations/schema_upgrade.py").read_text(encoding="utf-8")
        body = src.split("async def _migration_v58_manual_bans_without_user", 1)[1].split("\n\n\n", 1)[0]
        self.assertIn("ALTER COLUMN user_id DROP NOT NULL", body)
        self.assertIn("ADD PRIMARY KEY (id)", body)
        self.assertIn("uq_manual_bans_user_id ON manual_bans (user_id)", body)
        self.assertIn("(58, ", src.split("_MIGRATIONS = [", 1)[1])


# --- Заглушки апдейтов --------------------------------------------------------------------------


class FakeState:
    cleared = False

    async def clear(self):
        self.cleared = True

    async def set_state(self, state):
        pass


class FakeBot:
    def __init__(self, owner):
        self.owner = owner

    async def download(self, document, destination):
        self.owner.downloaded = True
        destination.write(document.payload)


class DocMessage:
    def __init__(self, payload, size=None, text=None):
        self.document = (
            None if payload is None else types.SimpleNamespace(payload=payload, file_size=size or len(payload))
        )
        self.text = text
        self.from_user = types.SimpleNamespace(id=77)
        self.bot = FakeBot(self)
        self.downloaded = False
        self.sent = []

    async def answer(self, text=None, **kw):
        self.sent.append(text)


class ExportCallback:
    def __init__(self):
        self.message = types.SimpleNamespace(documents=[], answer_document=self._doc, answer=self._answer)

    async def _doc(self, document, caption=None):
        self.message.documents.append(document)

    async def _answer(self, text=None, **kw):
        raise AssertionError(text)


if __name__ == "__main__":
    unittest.main()
