"""Предбан по tg id: бан заранее для тех, кого ещё нет в базе бота.

manual_bans держится на users.id, поэтому забанить ещё не пришедшего человека
было нельзя — только создать ему пустую учётку. Предбан живёт в своей таблице
по tg id и отсекает апдейт до регистрации. Ядро подменено заглушками.
Запуск из корня проекта: ``venv/bin/python -m unittest tests.test_preban -v``
"""

import ast
import asyncio
import pathlib
import sys
import types
import unittest

from datetime import timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_ban_public_reason import (  # noqa: E402
    INTERNAL,
    PUBLIC,
    LOG,
    CallbackQuery,
    Col,
    Message,
    _class_columns,
    _exec,
    _module,
)

UNKNOWN = 7000001  # выдуманные id
CLIENT = 7000002


def run(coro):
    return asyncio.run(coro)


class Tagged(Col):
    """Колонка с именем таблицы: по ней заглушка сессии понимает, какую таблицу спросили."""

    def __init__(self, table, name=None):
        self.table = table
        self.name = name

    def in_(self, ids):
        return ("in", self.table, list(ids))


class Query:
    def __init__(self, *cols):
        self.table = cols[0].__dict__.get("table") if cols else None
        self.cols = [c.__dict__.get("name") for c in cols]
        self.ids = None

    def select_from(self, model):
        self.table = model.table
        return self

    def where(self, *conds):
        for c in conds:
            if isinstance(c, tuple) and c[0] == "in":
                self.ids = c[2]
        return self

    def limit(self, *a):
        return self


def _table(name, *cols):
    ns = types.SimpleNamespace(table=name, **{c: Tagged(name, c) for c in cols})
    return ns


MANUAL = _table("manual_bans", "reason", "public_reason", "until", "user_id", "tg_id")
PRE = _table("pre_bans", "tg_id", "reason", "public_reason", "source", "created_at")
USERS = _table("users", "tg_id", "id")


# --- Клиентская сторона: middlewares/ban_checker.py ---------------------------------------------


class Result:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row


class CheckerSession:
    """manual_row — готовая строка; preban — запись предбана, отдаются ровно запрошенные колонки."""

    def __init__(self, manual_row=None, preban=None):
        self.manual_row = manual_row
        self.preban = preban
        self.tables = []

    async def execute(self, query):
        self.tables.append(query.table)
        if query.table == "manual_bans":
            return Result(self.manual_row)
        return Result(tuple(self.preban[c] for c in query.cols) if self.preban else None)


def preban(public=None):
    return {"tg_id": UNKNOWN, "reason": INTERNAL, "public_reason": public, "source": "list.txt", "created_at": None}


def _load_checker(cache, registered):
    async def cache_get(key):
        return cache.get(key)

    async def cache_set(key, value, ttl):
        cache[key] = value

    async def cache_delete(key):
        cache.pop(key, None)

    async def resolve_uid_cached(session, tg_id):
        return 1 if tg_id in registered else None

    stubs = {
        "pytz": _module("pytz", timezone=lambda name: timezone(timedelta(hours=3))),
        "aiogram": _module("aiogram", BaseMiddleware=object),
        "aiogram.types": _module(
            "aiogram.types", CallbackQuery=CallbackQuery, Message=Message, TelegramObject=object, Update=type("U", (), {})
        ),
        "sqlalchemy": _module("sqlalchemy", select=Query),
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
        "database.models": _module("database.models", ManualBan=MANUAL, PreBan=PRE),
        "logger": _module("logger", logger=LOG),
        "settings": _module("settings"),
        "settings.cache_config": _module("settings.cache_config", BAN_CACHE_TTL_SEC=60),
        "settings.config": _module("settings.config", ADMIN_ID=[999], SUPPORT_CHAT_URL="SUPPORT"),
    }
    return _exec("_ban_checker_preban_under_test", ROOT / "middlewares/ban_checker.py", stubs)


class PreBanStopsUpdate(unittest.TestCase):
    def setUp(self):
        self.cache = {}
        self.mod = _load_checker(self.cache, registered={CLIENT})
        self.passed = []

    async def _handler(self, event, data):
        self.passed.append(event)

    def _hit(self, event, **rows):
        session = CheckerSession(**rows)
        run(self.mod.BanCheckerMiddleware()(self._handler, event, {"session": session}))
        return session

    def test_unregistered_prebanned_is_stopped_before_handler(self):
        msg = Message(UNKNOWN)
        self._hit(msg, preban=preban(PUBLIC))
        self.assertEqual(self.passed, [], "хендлер /start и регистрация не должны получить апдейт")
        self.assertEqual(len(msg.sent), 1)
        self.assertIn(PUBLIC, msg.sent[0])

    def test_client_never_sees_internal_reason(self):
        msg = Message(UNKNOWN)
        session = self._hit(msg, preban=preban())
        self.assertIn(self.mod.DEFAULT_PUBLIC_BAN_REASON, msg.sent[0])
        self.assertNotIn(INTERNAL, msg.sent[0])
        self.assertNotIn(INTERNAL, str(self.cache))
        self.assertEqual(session.tables, ["pre_bans"])

    def test_callback_alert_for_prebanned(self):
        cb = CallbackQuery(UNKNOWN)
        self._hit(cb, preban=preban(PUBLIC))
        self.assertEqual(self.passed, [])
        self.assertIn(PUBLIC, cb.sent[0])

    def test_preban_is_cached(self):
        self._hit(Message(UNKNOWN), preban=preban(PUBLIC))
        second = Message(UNKNOWN)
        session = self._hit(second, preban=None)
        self.assertEqual(session.tables, [], "второй заход — из кэша")
        self.assertEqual(self.passed, [])
        self.assertIn(PUBLIC, second.sent[0])

    def test_registered_client_with_preban_is_stopped(self):
        msg = Message(CLIENT)
        session = self._hit(msg, manual_row=None, preban=preban())
        self.assertEqual(session.tables, ["manual_bans", "pre_bans"])
        self.assertEqual(self.passed, [])

    def test_ordinary_ban_still_works_and_wins(self):
        msg = Message(CLIENT)
        session = self._hit(msg, manual_row=(INTERNAL, PUBLIC, None), preban=None)
        self.assertEqual(session.tables, ["manual_bans"])
        self.assertEqual(self.passed, [])
        self.assertIn(PUBLIC, msg.sent[0])
        self.assertNotIn(INTERNAL, msg.sent[0])

    def test_nobody_banned_passes(self):
        msg = Message(UNKNOWN)
        self._hit(msg)
        self.assertEqual(self.passed, [msg])
        self.assertEqual(msg.sent, [])

    def test_admin_passes_even_if_prebanned(self):
        msg = Message(999)
        self._hit(msg, preban=preban(PUBLIC))
        self.assertEqual(self.passed, [msg])

    def test_invalidate_drops_preban_cache(self):
        self._hit(Message(UNKNOWN), preban=None)
        self.assertIn(f"preban_status:{UNKNOWN}", self.cache)
        run(self.mod.invalidate_ban_cache(UNKNOWN))
        self.assertNotIn(f"preban_status:{UNKNOWN}", self.cache)


# --- Разбор файла и загрузка: handlers/admin/bans/bans_handler.py -------------------------------

SAMPLE = "\n".join([
    "7001 #Причина бана: сканировал адреса http://host/a/b#frag",
    "7002 # текст",
    "7003 # Шаринг",
    "",
    "# комментарий",
    "мусор",
    "12ab #x",
    "-5 #минус",
    "0",
    "7004",
    "7002 #повтор",
    "99999999999999999999 #не влезает в bigint",
    "   7005   #  с пробелами  ",
])


class Router:
    def _deco(self, *a, **k):
        return lambda fn: fn

    def filter(self, *a, **k):
        return None

    def __init__(self):
        self.callback_query = self
        self.message = self

    def __call__(self, *a, **k):
        return lambda fn: fn


class Callback:
    @staticmethod
    def filter(*a, **k):
        return None


class StatesGroup:
    pass


class Insert:
    def __init__(self, session):
        self.session = session
        self.excluded = types.SimpleNamespace(reason="EXCLUDED.reason", source="EXCLUDED.source")

    def values(self, rows):
        self.rows = rows
        return self

    def on_conflict_do_update(self, index_elements, set_):
        self.set_ = set_
        return self


class Scalar:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class UploadSession:
    def __init__(self, prebans, users):
        self.existing = {"pre_bans": set(prebans), "users": set(users)}
        self.inserts = []

    async def execute(self, stmt):
        if isinstance(stmt, Insert):
            self.inserts.append(stmt)
            return None
        return Scalar(len(self.existing[stmt.table] & set(stmt.ids)))


class Doc:
    def __init__(self, text, name="list.txt"):
        self.data = text.encode()
        self.file_name = name
        self.file_size = len(self.data)


class AdminMessage:
    def __init__(self, document):
        self.document = document
        self.from_user = types.SimpleNamespace(id=5)
        self.sent = []

        async def download(doc, destination):
            destination.write(doc.data)

        self.bot = types.SimpleNamespace(download=download)

    async def answer(self, text=None, **kw):
        self.sent.append(text)


class FakeState:
    def __init__(self):
        self.cleared = False

    async def clear(self):
        self.cleared = True


def _load_bans(session_holder, invalidated):
    async def invalidate(tid):
        invalidated.append(tid)

    async def noop(*a, **k):
        return None

    models = _module(
        "database.models",
        BlockedUser=_table("blocked_users", "user_id"),
        Key=_table("keys", "user_id"),
        ManualBan=MANUAL,
        PreBan=PRE,
        User=USERS,
    )
    stubs = {
        "aiogram": _module("aiogram", F=Col(), Router=Router),
        "aiogram.fsm": _module("aiogram.fsm"),
        "aiogram.fsm.context": _module("aiogram.fsm.context", FSMContext=object),
        "aiogram.fsm.state": _module("aiogram.fsm.state", State=lambda: "state", StatesGroup=StatesGroup),
        "aiogram.types": _module("aiogram.types", BufferedInputFile=object, CallbackQuery=object, Message=object),
        "aiogram.utils": _module("aiogram.utils"),
        "aiogram.utils.keyboard": _module("aiogram.utils.keyboard", InlineKeyboardBuilder=object),
        "sqlalchemy": _module("sqlalchemy", delete=noop, func=Col(), or_=lambda *a: None, select=Query),
        "sqlalchemy.dialects": _module("sqlalchemy.dialects"),
        "sqlalchemy.dialects.postgresql": _module(
            "sqlalchemy.dialects.postgresql", insert=lambda m: Insert(session_holder)
        ),
        "sqlalchemy.ext": _module("sqlalchemy.ext"),
        "sqlalchemy.ext.asyncio": _module("sqlalchemy.ext.asyncio", AsyncSession=object),
        "database": _module("database", delete_user_data=noop),
        "database.access": _module("database.access"),
        "database.access.resolution": _module(
            "database.access.resolution", TelegramId=int, UserId=int, resolve_user_optional=noop
        ),
        "database.models": models,
        "database.users": _module("database.users", add_user=noop),
        "filters": _module("filters"),
        "filters.admin": _module("filters.admin", HasPermission=lambda *a: None, IsAdminFilter=lambda: None),
        "filters.permissions": _module("filters.permissions", PERM_MANAGEMENT="management"),
        "logger": _module("logger", logger=LOG),
        "middlewares": _module("middlewares"),
        "middlewares.ban_checker": _module("middlewares.ban_checker", invalidate_ban_cache=invalidate),
        "handlers": _module("handlers"),
        "handlers.admin": _module("handlers.admin"),
        "handlers.admin.panel": _module("handlers.admin.panel"),
        "handlers.admin.panel.headers": _module(
            "handlers.admin.panel.headers",
            menu_text=lambda *p, **k: "\n".join(p),
            quote=lambda *p: "\n".join(p),
            section=lambda *p: "\n".join(p),
            wrap_text=lambda t: t,
        ),
        "handlers.admin.panel.keyboard": _module("handlers.admin.panel.keyboard", AdminPanelCallback=Callback),
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


class ParsePrebanFile(unittest.TestCase):
    def setUp(self):
        self.mod = _load_bans(None, [])

    def test_sample(self):
        entries, skipped = self.mod.parse_preban_lines(SAMPLE)
        self.assertEqual(
            entries,
            {
                7001: "Причина бана: сканировал адреса http://host/a/b#frag",
                7002: "текст",
                7003: "Шаринг",
                7004: None,
                7005: "с пробелами",
            },
        )
        # мусор, 12ab, -5, 0, повтор 7002, слишком длинный id; пустая и «# ...» не считаются
        self.assertEqual(skipped, 6)

    def test_first_reason_wins_on_repeat(self):
        entries, _ = self.mod.parse_preban_lines("7101 #первая\n7101 #вторая")
        self.assertEqual(entries, {7101: "первая"})

    def test_nothing_valid(self):
        self.assertEqual(self.mod.parse_preban_lines("\n# только комментарий\n"), ({}, 0))


class UploadPrebanFile(unittest.TestCase):
    def setUp(self):
        self.invalidated = []
        self.mod = _load_bans(None, self.invalidated)

    def test_upsert_counts_and_no_manual_ban(self):
        # 7001 уже в предбане; 7001 и 7003 уже клиенты бота
        session = UploadSession(prebans={7001}, users={7001, 7003})
        msg, state = AdminMessage(Doc(SAMPLE)), FakeState()
        run(self.mod.handle_preban_file(msg, state, session))

        self.assertEqual(len(session.inserts), 1)
        ins = session.inserts[0]
        self.assertEqual(sorted(r["tg_id"] for r in ins.rows), [7001, 7002, 7003, 7004, 7005])
        self.assertTrue(all(r["source"] for r in ins.rows))
        self.assertNotIn("public_reason", ins.set_, "повторная загрузка не трогает публичную причину")
        self.assertIn("reason", ins.set_)
        report = msg.sent[-1]
        self.assertIn("Добавлено: <b>4</b>", report)
        self.assertIn("Обновлено: <b>1</b>", report)
        self.assertIn("Пропущено строк: <b>6</b>", report)
        self.assertIn("уже клиенты бота: <b>2</b>", report)
        self.assertEqual(sorted(self.invalidated), sorted(r["tg_id"] for r in ins.rows))
        self.assertTrue(state.cleared)

    def test_not_txt_rejected(self):
        session = UploadSession(prebans=(), users=())
        msg, state = AdminMessage(Doc("7001", name="list.csv")), FakeState()
        run(self.mod.handle_preban_file(msg, state, session))
        self.assertEqual(session.inserts, [])
        self.assertFalse(state.cleared)

    def test_no_document_rejected(self):
        session = UploadSession(prebans=(), users=())
        msg, state = AdminMessage(None), FakeState()
        run(self.mod.handle_preban_file(msg, state, session))
        self.assertEqual(session.inserts, [])

    def test_upload_never_writes_manual_bans(self):
        src = (ROOT / "handlers/admin/bans/bans_handler.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "handle_preban_file")
        names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
        self.assertNotIn("ManualBan", names, "по существующим клиентам обычный бан решает человек")
        self.assertNotIn("add_user", names, "учётки под предбан не заводим")


# --- Схема и права ------------------------------------------------------------------------------


class SchemaAndAcl(unittest.TestCase):
    def test_preban_model(self):
        cols = _class_columns("database/models/users.py", "PreBan")
        self.assertEqual(set(cols), {"tg_id", "reason", "public_reason", "source", "created_at"})
        pk = {k.arg: ast.unparse(k.value) for k in cols["tg_id"].keywords}
        self.assertEqual(pk.get("primary_key"), "True", "tg_id уникален")
        nullable = {k.arg: ast.unparse(k.value) for k in cols["public_reason"].keywords}.get("nullable", "True")
        self.assertEqual(nullable, "True")
        self.assertIn("__tablename__", (ROOT / "database/models/users.py").read_text(encoding="utf-8"))

    def test_table_created_by_startup_model_sync(self):
        # таблицу создаёт сверка моделей при старте: модель в Base.metadata, CreateTableOp — допустимая операция
        init = (ROOT / "database/models/__init__.py").read_text(encoding="utf-8")
        self.assertRegex(init, r"from \.users import [^\n]*\bPreBan\b")
        auto = ast.parse((ROOT / "database/migrations/autogenerate.py").read_text(encoding="utf-8"))
        additive = next(
            n
            for n in auto.body
            if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "ADDITIVE_OPS" for t in n.targets)
        )
        self.assertIn("ops.CreateTableOp", ast.unparse(additive.value))
        setup = (ROOT / "database/setup/init_db.py").read_text(encoding="utf-8")
        self.assertIn("apply_model_changes(conn, Base.metadata", setup)

    def test_acl_same_as_other_ban_actions(self):
        sys.path.insert(0, str(ROOT))
        src = (ROOT / "filters/admin_actions.py").read_text(encoding="utf-8")
        self.assertIn('action.startswith(("bans_", "shadow_bans_", "manual_bans_"))', src)
        self.assertIn('state.startswith(("BanUserStates:", "PreemptiveBanStates:"))', src)
        kb = (ROOT / "handlers/admin/bans/keyboard.py").read_text(encoding="utf-8")
        for action in ("bans_preban_upload", "bans_preban_export"):
            self.assertIn(f'action="{action}"', kb)
        handler = (ROOT / "handlers/admin/bans/bans_handler.py").read_text(encoding="utf-8")
        self.assertIn('action="bans_preban_cancel"', handler)
        self.assertIn("PreemptiveBanStates.waiting_for_preban_file", handler)

    def test_ban_checker_runs_before_user_registration(self):
        src = (ROOT / "middlewares/__init__.py").read_text(encoding="utf-8")
        self.assertLess(
            src.index("dispatcher.update.outer_middleware(BanCheckerMiddleware())"),
            src.index('"user": UserMiddleware()'),
        )
        self.assertIn("h.outer_middleware(middleware)", src, "UserMiddleware — уровень message, после update")


if __name__ == "__main__":
    unittest.main()
