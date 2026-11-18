"""Две причины бана: внутренняя видна только админам, клиенту — только публичная.

Раньше у бана была одна причина, и клиент видел её дословно — служебная
пометка админа уезжала клиенту в сообщении о блокировке.
Ядро подменено заглушками.
Запуск из корня проекта: ``venv/bin/python -m unittest tests.test_ban_public_reason -v``
"""

import ast
import asyncio
import importlib.util
import pathlib
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

INTERNAL = "внутренняя пометка для админов"
PUBLIC = "нарушение правил использования"


def run(coro):
    return asyncio.run(coro)


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


def _exec(name, path, stubs):
    saved = {k: sys.modules.get(k) for k in (*stubs, name)}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return module


LOG = types.SimpleNamespace(warning=lambda *a, **k: None, info=lambda *a, **k: None, debug=lambda *a, **k: None)


class Col:
    """Колонка-заглушка: любые операции SQL-выражений возвращают её же."""

    def __getattr__(self, name):
        return lambda *a, **k: self

    def __or__(self, other):
        return self

    def __gt__(self, other):
        return self

    def __eq__(self, other):
        return self

    __hash__ = object.__hash__


class Query:
    def where(self, *a):
        return self

    def limit(self, *a):
        return self


# --- Клиентская сторона: middlewares/ban_checker.py ---------------------------------------------


class Message:
    def __init__(self, tg_id):
        self.from_user = types.SimpleNamespace(id=tg_id)
        self.sent = []

    async def answer(self, text, **kwargs):
        self.sent.append(text)


class CallbackQuery(Message):
    pass


class Result:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row


class Session:
    def __init__(self, row):
        self.row = row
        self.queries = 0

    async def execute(self, query):
        self.queries += 1
        return Result(self.row)


def _load_checker(cache):
    async def cache_get(key):
        return cache.get(key)

    async def cache_set(key, value, ttl):
        cache[key] = value

    async def cache_delete(key):
        cache.pop(key, None)

    async def resolve_uid_cached(session, tg_id):
        return 1

    manual_ban = types.SimpleNamespace(reason=Col(), public_reason=Col(), until=Col(), user_id=Col())
    stubs = {
        "pytz": _module("pytz", timezone=lambda name: timezone(timedelta(hours=3))),
        "aiogram": _module("aiogram", BaseMiddleware=object),
        "aiogram.types": _module(
            "aiogram.types", CallbackQuery=CallbackQuery, Message=Message, TelegramObject=object, Update=type("U", (), {})
        ),
        "sqlalchemy": _module("sqlalchemy", select=lambda *a: Query()),
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
        "database.models": _module(
            "database.models", ManualBan=manual_ban, PreBan=types.SimpleNamespace(tg_id=Col(), public_reason=Col())
        ),
        "logger": _module("logger", logger=LOG),
        "settings": _module("settings"),
        "settings.cache_config": _module("settings.cache_config", BAN_CACHE_TTL_SEC=60),
        "settings.config": _module("settings.config", ADMIN_ID=[999], SUPPORT_CHAT_URL="SUPPORT"),
    }
    return _exec("_ban_checker_under_test", ROOT / "middlewares/ban_checker.py", stubs)


class ClientSeesOnlyPublicReason(unittest.TestCase):
    def setUp(self):
        self.cache = {}
        self.mod = _load_checker(self.cache)
        self.passed = []

    async def _handler(self, event, data):
        self.passed.append(event)

    def _hit(self, row, event):
        session = Session(row)
        run(self.mod.BanCheckerMiddleware()(self._handler, event, {"session": session}))
        return session

    def test_message_shows_public_not_internal(self):
        msg = Message(42)
        self._hit((INTERNAL, PUBLIC, None), msg)
        self.assertEqual(len(msg.sent), 1)
        self.assertIn(PUBLIC, msg.sent[0])
        self.assertNotIn(INTERNAL, msg.sent[0])
        self.assertEqual(self.passed, [])

    def test_temporary_ban_alert_shows_public_not_internal(self):
        cb = CallbackQuery(42)
        self._hit((INTERNAL, PUBLIC, datetime.now(timezone.utc) + timedelta(days=2)), cb)
        self.assertIn(PUBLIC, cb.sent[0])
        self.assertNotIn(INTERNAL, cb.sent[0])

    def test_old_ban_without_public_reason_gets_neutral_text(self):
        msg = Message(42)
        self._hit((INTERNAL, None, None), msg)
        self.assertIn(self.mod.DEFAULT_PUBLIC_BAN_REASON, msg.sent[0])
        self.assertNotIn(INTERNAL, msg.sent[0])

    def test_cached_ban_does_not_leak_internal(self):
        first, second = Message(42), Message(42)
        self._hit((INTERNAL, None, None), first)
        session = self._hit((INTERNAL, None, None), second)
        self.assertEqual(session.queries, 0, "второй заход должен прийти из кэша")
        self.assertNotIn(INTERNAL, str(self.cache))
        self.assertNotIn(INTERNAL, second.sent[0])

    def test_old_format_cache_entry_is_reloaded(self):
        # до правки кэш хранил внутреннюю причину под ключом reason
        self.cache["ban_status:42"] = {"user_id": 1, "has_ban": True, "reason": INTERNAL, "until": None}
        msg = Message(42)
        session = self._hit((INTERNAL, PUBLIC, None), msg)
        self.assertEqual(session.queries, 1)
        self.assertIn(PUBLIC, msg.sent[0])
        self.assertNotIn(INTERNAL, msg.sent[0])

    def test_shadow_ban_stays_silent(self):
        msg = Message(42)
        self._hit(("shadow", None, None), msg)
        self.assertEqual(msg.sent, [])
        self.assertEqual(self.passed, [])

    def test_not_banned_passes_through(self):
        msg = Message(42)
        self._hit(None, msg)
        self.assertEqual(self.passed, [msg])
        self.assertEqual(msg.sent, [])


# --- Схема: модель и механизм миграций ----------------------------------------------------------


def _class_columns(path, class_name):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    return {
        n.targets[0].id: n.value
        for n in cls.body
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and isinstance(n.value, ast.Call)
    }


class SchemaHasPublicReason(unittest.TestCase):
    def test_model_has_nullable_public_reason_and_keeps_reason(self):
        cols = _class_columns("database/models/users.py", "ManualBan")
        self.assertIn("reason", cols, "колонку внутренней причины не переименовываем")
        col = cols.get("public_reason")
        self.assertIsNotNone(col)
        self.assertEqual(ast.unparse(col.args[0]), "Text")
        nullable = {k.arg: ast.unparse(k.value) for k in col.keywords}.get("nullable", "True")
        self.assertEqual(nullable, "True", "старые баны живут с NULL")

    def test_autogenerate_adds_new_columns(self):
        # колонку добавляет сверка моделей при старте, отдельный шаг миграции не нужен
        src = (ROOT / "database/migrations/autogenerate.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        additive = next(
            n
            for n in tree.body
            if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "ADDITIVE_OPS" for t in n.targets)
        )
        self.assertIn("ops.AddColumnOp", ast.unparse(additive.value))

    def test_api_schema_exposes_both_reasons_to_admins(self):
        cols = {
            n.target.id
            for n in ast.walk(ast.parse((ROOT / "api/v1/schemas/misc.py").read_text(encoding="utf-8")))
            if isinstance(n, ast.ClassDef) and n.name == "ManualBanResponse"
            for n in n.body
            if isinstance(n, ast.AnnAssign)
        }
        self.assertTrue({"reason", "public_reason"} <= cols)


    def test_fsm_has_public_reason_stage(self):
        cols = _class_columns("handlers/admin/users/users_states.py", "BanUserStates")
        self.assertEqual(set(cols), set(vars(BanUserStates)), "заглушка состояний совпадает с настоящими")


# --- Админка: handlers/admin/users/users_bans.py ------------------------------------------------


class FakeState:
    def __init__(self, data):
        self.data, self.state = dict(data), None

    async def set_state(self, state):
        self.state = state

    async def update_data(self, **kw):
        self.data.update(kw)

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.data, self.state = {}, None


class Insert:
    def __init__(self, saved):
        self.saved = saved

    def values(self, **kw):
        self.saved["values"] = kw
        return self

    def on_conflict_do_update(self, index_elements, set_):
        self.saved["set_"] = set_
        return self


class AdminMessage:
    def __init__(self, text):
        self.text = text
        self.from_user = types.SimpleNamespace(id=5)
        self.sent = []

    async def answer(self, text=None, **kw):
        self.sent.append(text)


class Router:
    def _deco(self, *a, **k):
        return lambda fn: fn

    callback_query = message = _deco


class Builder:
    def row(self, *a):
        return self

    def as_markup(self):
        return None


BanUserStates = types.SimpleNamespace(
    waiting_for_reason="reason",
    waiting_for_ban_duration="duration",
    waiting_for_forever_reason="forever",
    waiting_for_public_reason="public",
)


def _load_bans(saved):
    async def resolve_user_optional(session, user_id):
        return types.SimpleNamespace(id=10, tg_id=20)

    async def noop(*a, **k):
        return None

    class EditorCallback:
        @staticmethod
        def filter(*a, **k):
            return lambda *x, **y: True

    stubs = {
        "aiogram": _module("aiogram", F=Col(), Router=Router),
        "aiogram.fsm": _module("aiogram.fsm"),
        "aiogram.fsm.context": _module("aiogram.fsm.context", FSMContext=object),
        "aiogram.types": _module("aiogram.types", CallbackQuery=object, Message=object),
        "aiogram.utils": _module("aiogram.utils"),
        "aiogram.utils.keyboard": _module("aiogram.utils.keyboard", InlineKeyboardBuilder=Builder),
        "sqlalchemy": _module("sqlalchemy", delete=lambda *a: None),
        "sqlalchemy.dialects": _module("sqlalchemy.dialects"),
        "sqlalchemy.dialects.postgresql": _module("sqlalchemy.dialects.postgresql", insert=lambda m: Insert(saved)),
        "sqlalchemy.ext": _module("sqlalchemy.ext"),
        "sqlalchemy.ext.asyncio": _module("sqlalchemy.ext.asyncio", AsyncSession=object),
        "database": _module("database"),
        "database.access": _module("database.access"),
        "database.access.resolution": _module(
            "database.access.resolution", UserId=int, resolve_user_optional=resolve_user_optional
        ),
        "database.models": _module("database.models", ManualBan=types.SimpleNamespace(user_id=Col())),
        "filters": _module("filters"),
        "filters.admin": _module("filters.admin", IsAdminFilter=lambda: (lambda *a, **k: True)),
        "middlewares": _module("middlewares"),
        "middlewares.ban_checker": _module(
            "middlewares.ban_checker", DEFAULT_PUBLIC_BAN_REASON="не указана", invalidate_ban_cache=noop
        ),
        "settings": _module("settings"),
        "settings.buttons": _module("settings.buttons", BACK="Назад"),
        "handlers": _module("handlers"),
        "handlers.admin": _module("handlers.admin"),
        "handlers.admin.panel": _module("handlers.admin.panel"),
        "handlers.admin.panel.headers": _module(
            "handlers.admin.panel.headers", menu_text=lambda *p, **k: "\n".join(p), quote=lambda *p: "\n".join(p)
        ),
        "handlers.admin.users": _module("handlers.admin.users"),
        "handlers.admin.users.keyboard": _module(
            "handlers.admin.users.keyboard",
            AdminUserEditorCallback=EditorCallback,
            build_editor_btn=lambda *a, **k: None,
            build_editor_kb=lambda *a, **k: None,
            build_user_ban_type_kb=lambda *a, **k: None,
        ),
        "handlers.admin.users.users_states": _module("handlers.admin.users.users_states", BanUserStates=BanUserStates),
    }
    stubs["handlers.admin.users"].__path__ = [str(ROOT / "handlers/admin/users")]
    return _exec("handlers.admin.users.users_bans", ROOT / "handlers/admin/users/users_bans.py", stubs)


class AdminEntersBothReasons(unittest.TestCase):
    def setUp(self):
        self.saved = {}
        self.mod = _load_bans(self.saved)

    def test_forever_ban_saves_both_reasons(self):
        st = FakeState({"user_id": 10})
        run(self.mod.handle_ban_forever_reason_input(AdminMessage(INTERNAL), st))
        self.assertEqual(st.state, self.mod.BanUserStates.waiting_for_public_reason)
        self.assertEqual(self.saved, {}, "до публичной причины бан не ставится")

        reply = AdminMessage(PUBLIC)
        run(self.mod.handle_ban_public_reason_input(reply, st, session=types.SimpleNamespace(execute=_noop_exec)))
        self.assertEqual(self.saved["values"]["reason"], INTERNAL)
        self.assertEqual(self.saved["values"]["public_reason"], PUBLIC)
        self.assertIsNone(self.saved["values"]["until"])
        self.assertEqual(self.saved["set_"]["public_reason"], PUBLIC, "повторный бан перезаписывает публичную")
        self.assertIn(INTERNAL, reply.sent[-1])
        self.assertIn(PUBLIC, reply.sent[-1])

    def test_temporary_ban_without_public_reason(self):
        st = FakeState({"user_id": 10})
        run(self.mod.handle_ban_reason_input(AdminMessage(INTERNAL), st))
        run(self.mod.handle_ban_public_reason_input(AdminMessage("-"), st, session=None))
        self.assertEqual(st.state, self.mod.BanUserStates.waiting_for_ban_duration)

        reply = AdminMessage("3")
        run(self.mod.handle_ban_duration_input(reply, st, session=types.SimpleNamespace(execute=_noop_exec)))
        self.assertEqual(self.saved["values"]["reason"], INTERNAL)
        self.assertIsNone(self.saved["values"]["public_reason"])
        self.assertIsNotNone(self.saved["values"]["until"])
        self.assertIn("не указана", reply.sent[-1])


async def _noop_exec(stmt):
    return None


if __name__ == "__main__":
    unittest.main()
