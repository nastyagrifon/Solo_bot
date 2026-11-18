"""Сквады и тег тестировщиков одной настройкой и права роли tester.

Без настройки тестировщиков настраивали в панели руками по одному, а в
карточке клиента тестировщик мог менять тариф и поля панели, удалять и
переносить подписки, отвязывать устройства — наравне с админом.
Ядро подменено заглушками, панель — фейком.
Запуск из корня проекта: ``venv/bin/python -m unittest discover -s tests -v``
"""

import ast
import asyncio
import importlib.util
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PKG = ROOT / "handlers/admin/users/panel_user"
MGMT = ROOT / "handlers/admin/management"

from filters.permissions import (  # noqa: E402
    ALL_ACTION_PERMISSIONS,
    GRANULAR_ACTIONS_CONFIGURED,
    PERM_ADMINS,
    PERM_KEY_DELETE,
    PERM_KEY_DEVICES,
    PERM_KEY_TARIFF,
    PERM_KEY_VIEW,
    action_permissions,
)

try:
    from aiogram import Router
    from aiogram.filters.callback_data import CallbackData
    from aiogram.types import CallbackQuery, User
except ImportError:
    CallbackData = None


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


# --- Логика: users/panel_user/testers.py --------------------------------------------------------


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class FakeApi:
    def __init__(self, user, status=200):
        self.user, self.status, self.patches = dict(user), status, []

    async def get_user_by_uuid(self, uuid, username=None):
        return dict(self.user)

    async def _request(self, method, path, json=None, params=None):
        if self.status != 200:
            return FakeResponse(self.status)
        if method == "GET":
            kind = path.strip("/").split("-")[0]
            return FakeResponse(200, {"response": {f"{kind}Squads": [{"uuid": f"{kind}-1", "name": kind}]}})
        self.patches.append(json)
        self.user.update({k: v for k, v in json.items() if k not in ("uuid", "id")})
        return FakeResponse(200, {"response": dict(self.user)})


class Key:
    user_id = "user_id"


class Admin:
    tg_id, role = "tg_id", "role"

    def __init__(self, **fields):
        self.__dict__.update(fields)


class Query:
    def __init__(self, cols):
        self.cols = cols

    def where(self, *a):
        return self


class Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)


class Panel:
    """Панель по client_id; ключи клиентов по tg_id; строки admins."""

    def __init__(self):
        self.apis, self.keys, self.admins, self.config, self.servers = {}, {}, [], {}, set()
        self.current_tg = None


P = Panel()


class Session:
    async def execute(self, query):
        if query.cols[0] is Key:
            return Result(P.keys.get(P.current_tg, []))
        return Result(P.admins)


def _load_testers():
    async def with_remnawave_api(session, server_ref, operation, **kwargs):
        api = P.apis.get(kwargs.get("client_id")) or P.apis.get("any")
        return None if api is None else await operation(api)

    async def noop(*a, **k):
        return None

    async def get_user_by_tg_id(session, tg_id):
        P.current_tg = tg_id
        return types.SimpleNamespace(id=tg_id) if tg_id in P.keys else None

    async def resolve_ref(session, server_id):
        return server_id if server_id in P.servers else None

    async def update_management_config(session, values):
        P.config.clear()
        P.config.update(values)

    runtime = _module("panels.remnawave_runtime", with_remnawave_api=with_remnawave_api,
                      invalidate_remnawave_profile=noop)
    package = _module("kpu_pkg")
    package.__path__ = [str(PKG)]
    stubs = {
        "kpu_pkg": package,
        "panels": _module("panels"),
        "panels.remnawave_runtime": runtime,
        "sqlalchemy": _module("sqlalchemy", select=lambda *cols: Query(cols)),
        "core": _module("core"),
        "core.settings": _module("core.settings"),
        "core.settings.management_config": _module(
            "core.settings.management_config", MANAGEMENT_CONFIG=P.config,
            update_management_config=update_management_config,
        ),
        "database": _module("database"),
        "database.access": _module("database.access"),
        "database.access.resolution": _module("database.access.resolution", get_user_by_tg_id=get_user_by_tg_id),
        "database.models": _module("database.models", Admin=Admin, Key=Key),
        "logger": _module("logger", logger=LOG),
        "services": _module("services"),
        "services.subscription_keys": _module("services.subscription_keys", resolve_remnawave_server_ref=resolve_ref),
    }
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        service = _exec("kpu_pkg.service", PKG / "service.py", {})
        sys.modules["kpu_pkg.service"] = service
        package.service = service
        return _exec("kpu_pkg.testers", PKG / "testers.py", {})
    finally:
        sys.modules.pop("kpu_pkg.service", None)
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _key(cid, email, server="srv"):
    return types.SimpleNamespace(client_id=cid, email=email, server_id=server, remnawave_link=None, key=None)


class TestersBase(unittest.TestCase):
    def setUp(self):
        P.__init__()
        self.t = _load_testers()
        P.servers = {"srv"}


class StorageTest(TestersBase):
    def test_saved_once_and_read_back(self):
        P.config["OTHER"] = 1
        run(self.t.save_external(None, "ext-1"))
        run(self.t.save_internal(None, ["a", "b"]))
        self.assertEqual(self.t.configured(), (True, "ext-1", ["a", "b"]))
        # Соседние настройки управления не теряются.
        self.assertEqual(P.config["OTHER"], 1)

    def test_without_external_choice_external_is_not_set(self):
        run(self.t.save_internal(None, ["a"]))
        self.assertEqual(self.t.configured(), (False, None, ["a"]))

    def test_no_external_squad_is_a_choice(self):
        run(self.t.save_external(None, None))
        self.assertEqual(self.t.configured()[:2], (True, None))

    def test_squad_lists_from_panel(self):
        P.apis["any"] = FakeApi({})
        external, internal = run(self.t.panel_squads(None))
        self.assertEqual((external[0]["name"], internal[0]["name"]), ("external", "internal"))

    def test_squad_lists_panel_down_is_error(self):
        with self.assertRaises(self.t.service.PanelUserError):
            run(self.t.panel_squads(None))


class ApplyTest(TestersBase):
    def setUp(self):
        super().setUp()
        P.config.update({self.t.EXTERNAL: "ext-1", self.t.INTERNAL: ["in-1", "in-2"]})
        P.keys[7] = [_key("c1", "a@x"), _key("c2", "b@x"), _key("c3", "xui@x", server="xui")]
        P.apis = {cid: FakeApi({"uuid": f"u-{cid}", "tag": None}) for cid in ("c1", "c2", "c3")}

    def test_one_patch_with_squads_and_tag_per_remnawave_key(self):
        self.assertEqual(run(self.t.apply(Session(), 7, True)), (2, [], []))
        for cid in ("c1", "c2"):
            self.assertEqual(P.apis[cid].patches, [{
                "uuid": f"u-{cid}", "externalSquadUuid": "ext-1",
                "activeInternalSquads": ["in-1", "in-2"], "tag": "TESTER",
            }])
        # Подписка не на Remnawave не трогается.
        self.assertEqual(P.apis["c3"].patches, [])

    def test_failure_of_one_key_does_not_stop_others(self):
        P.apis["c1"].status = 500
        done, failed, _ = run(self.t.apply(Session(), 7, True))
        self.assertEqual((done, failed), (1, ["a@x"]))
        self.assertEqual(len(P.apis["c2"].patches), 1)
        self.assertIn("a@x", self.t.report(done, failed, []))

    def test_panel_ignoring_one_field_is_failure(self):
        api = P.apis["c2"]
        original = api._request

        async def ignore_squads(method, path, json=None, params=None):
            # Сверяется каждое поле, а не только первое.
            dropped = {k: v for k, v in (json or {}).items() if k != "activeInternalSquads"}
            return await original(method, path, dropped, params)

        api._request = ignore_squads
        self.assertEqual(run(self.t.apply(Session(), 7, True))[1], ["b@x"])

    def test_unset_parts_are_not_sent(self):
        P.config.clear()
        run(self.t.apply(Session(), 7, True))
        self.assertEqual(P.apis["c1"].patches, [{"uuid": "u-c1", "tag": "TESTER"}])

    def test_removal_clears_only_tester_tag(self):
        P.apis["c1"].user["tag"] = "TESTER"
        P.apis["c2"].user["tag"] = "VIP"
        self.assertEqual(run(self.t.apply(Session(), 7, False)), (1, [], []))
        self.assertEqual(P.apis["c1"].patches, [{"uuid": "u-c1", "tag": None}])
        self.assertEqual(P.apis["c2"].patches, [])

    def test_apply_all_only_testers(self):
        P.keys[8] = [_key("c9", "admin@x")]
        P.apis["c9"] = FakeApi({"uuid": "u-c9"})
        P.admins = [(7, "Tester"), (8, "admin")]
        self.assertEqual(run(self.t.apply_all(Session())), (2, [], []))
        self.assertEqual(P.apis["c9"].patches, [])

    def test_bot_user_unknown_is_nothing_to_do(self):
        self.assertEqual(run(self.t.apply(Session(), 999, True)), (0, [], []))

    def test_foreign_tag_is_kept_and_reported(self):
        P.apis["c1"].user["tag"] = "VIP"
        P.apis["c2"].user["tag"] = "TESTER"
        done, failed, kept = run(self.t.apply(Session(), 7, True))
        self.assertEqual((done, failed, kept), (2, [], ["a@x"]))
        # Сквады встают, тег — нет.
        self.assertEqual(P.apis["c1"].patches, [{
            "uuid": "u-c1", "externalSquadUuid": "ext-1", "activeInternalSquads": ["in-1", "in-2"],
        }])
        self.assertEqual(P.apis["c1"].user["tag"], "VIP")
        self.assertEqual(P.apis["c2"].patches[0]["tag"], "TESTER")
        self.assertIn("Чужой тег оставлен", self.t.report(done, failed, kept))
        self.assertIn("a@x", self.t.report(done, failed, kept))

    def test_foreign_tag_only_nothing_to_send(self):
        P.config.clear()
        P.apis["c1"].user["tag"] = "VIP"
        self.assertEqual(run(self.t.apply(Session(), 7, True)), (1, [], ["a@x"]))
        self.assertEqual(P.apis["c1"].patches, [])


# --- Назначение роли: management/admins.py ----------------------------------------------------


def _mgmt_stubs(applied):
    async def apply(session, tg_id, tester):
        applied.append((tg_id, tester))
        return 1, []

    class AdminPanelCallback(CallbackData, prefix="admin_panel"):
        action: str
        page: int = 0

    async def noop(*a, **k):
        return None

    testers = _module("handlers.admin.users.panel_user.testers", apply=apply, report=lambda d, f: f"изменено {d}",
                      TESTER_TAG="TESTER", configured=lambda: (True, None, []))
    service = _module("handlers.admin.users.panel_user.service", PanelUserError=Exception,
                      toggle=lambda s, u: s)
    package = _module("handlers.admin.management", router=Router())
    package.__path__ = [str(MGMT)]
    query = types.SimpleNamespace(where=lambda *a: query, with_for_update=lambda: query)
    headers = _module("handlers.admin.panel.headers", menu_text=lambda *p, **k: "\n".join(p),
                      quote=lambda *p, **k: "\n".join(p), section=lambda t, *l: "\n".join((t, *l)))
    kb = {n: (lambda *a, **k: None) for n in (
        "build_admin_action_permissions_kb", "build_admin_back_kb_to_admins", "build_admin_permissions_kb",
        "build_admins_kb", "build_new_admin_role_kb", "build_role_selection_kb", "build_single_admin_menu",
        "build_token_result_kb")}
    return {
        "sqlalchemy": _module("sqlalchemy", select=lambda *a: query, delete=lambda *a: query),
        "sqlalchemy.ext": _module("sqlalchemy.ext"),
        "sqlalchemy.ext.asyncio": _module("sqlalchemy.ext.asyncio", AsyncSession=object),
        "database": _module("database"),
        "database.identities": _module("database.identities", get_identity_by_tg_id=noop),
        "database.identity_sessions": _module("database.identity_sessions", delete_other_sessions=noop),
        "database.models": _module("database.models", Admin=Admin),
        "filters.admin": _module("filters.admin", HasPermission=lambda *p: (lambda *a, **k: True),
                                 invalidate_admin_cache=lambda *a: None),
        "filters.admin_actions": _module("filters.admin_actions", granular_permissions_enabled=lambda: False),
        "settings": _module("settings"),
        "settings.buttons": _module("settings.buttons", BACK="Назад"),
        "handlers": _module("handlers"),
        "handlers.admin": _module("handlers.admin"),
        "handlers.admin.panel": _module("handlers.admin.panel"),
        "handlers.admin.panel.headers": headers,
        "handlers.admin.panel.keyboard": _module("handlers.admin.panel.keyboard",
                                                 AdminPanelCallback=AdminPanelCallback),
        "handlers.admin.users": _module("handlers.admin.users"),
        "handlers.admin.users.panel_user": _module("handlers.admin.users.panel_user", testers=testers),
        "handlers.admin.users.panel_user.testers": testers,
        "handlers.admin.users.panel_user.service": service,
        "handlers.admin.management": package,
        "handlers.admin.management.keyboard": _module(
            "handlers.admin.management.keyboard", AdminPanelCallback=AdminPanelCallback, **kb),
    }, AdminPanelCallback


class FakeMessage:
    def __init__(self):
        self.texts = []

    async def edit_text(self, text, **k):
        self.texts.append(text)


class FakeCallback:
    def __init__(self, user_id=1):
        self.from_user = types.SimpleNamespace(id=user_id)
        self.message = FakeMessage()
        self.answers = []

    async def answer(self, *a, **k):
        self.answers.append(a)


class AdminSession:
    def __init__(self, admin=None, role=None):
        self.admin, self.role, self.added = admin, role, []

    async def execute(self, query):
        admin, role = self.admin, self.role
        return types.SimpleNamespace(scalar_one_or_none=lambda: admin if admin is not None else role)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


@unittest.skipIf(CallbackData is None, "aiogram не установлен")
class RoleHookTest(unittest.TestCase):
    def setUp(self):
        self.applied = []
        stubs, self.cb = _mgmt_stubs(self.applied)
        self.m = _exec("handlers.admin.management.admins", MGMT / "admins.py", stubs)

    def _set_role(self, old, new):
        admin = types.SimpleNamespace(role=old)
        callback = FakeCallback()
        run(self.m.set_admin_role(callback, self.cb(action=f"set_role|7|{new}"), AdminSession(admin)))
        return callback

    def test_becoming_tester_applies(self):
        callback = self._set_role("admin", "tester")
        self.assertEqual(self.applied, [(7, True)])
        self.assertIn("изменено 1", callback.message.texts[-1])

    def test_leaving_tester_removes_tag(self):
        self._set_role("tester", "support")
        self.assertEqual(self.applied, [(7, False)])

    def test_other_roles_untouched(self):
        self._set_role("admin", "support")
        self._set_role("tester", "tester")
        self.assertEqual(self.applied, [])

    def test_new_tester_applies(self):
        session = AdminSession()
        run(self.m.create_admin_with_role(FakeCallback(), self.cb(action="add_role|7|tester"), session))
        self.assertEqual(self.applied, [(7, True)])

    def test_deleted_tester_removes_tag(self):
        run(self.m.delete_admin(FakeCallback(), self.cb(action="delete_admin|7"), AdminSession(role="tester")))
        self.assertEqual(self.applied, [(7, False)])


# --- Экран настройки: management/testers.py ----------------------------------------------------


@unittest.skipIf(CallbackData is None, "aiogram не установлен")
class ScreenTest(unittest.TestCase):
    def setUp(self):
        stubs, _ = _mgmt_stubs([])
        self.s = _exec("handlers.admin.management.testers", MGMT / "testers.py", stubs)

    def test_every_handler_needs_admins_right(self):
        tree = ast.parse((MGMT / "testers.py").read_text(encoding="utf-8"))
        handlers = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                    and any("router." in ast.unparse(d) for d in n.decorator_list)]
        self.assertGreaterEqual(len(handlers), 6)
        for node in handlers:
            with self.subTest(handler=node.name):
                self.assertIn("HasPermission(PERM_ADMINS)", " ".join(ast.unparse(d) for d in node.decorator_list))
        self.assertEqual(self.s.PERM_ADMINS, PERM_ADMINS)

    def test_buttons_fit_64_bytes(self):
        squads = [[f"uuid-{i}" * 6, "имя" * 30] for i in range(120)]
        markups = [self.s.main_kb(), self.s._internal_kb(squads, [])]
        for markup in markups:
            for row in markup.inline_keyboard:
                for button in row:
                    self.assertLessEqual(len(button.callback_data.encode()), 64, button.callback_data)


# --- Права ------------------------------------------------------------------------------------


def _load_actions(config=None):
    return _exec("filters.admin_actions", ROOT / "filters/admin_actions.py", {
        "core": _module("core"),
        "core.settings": _module("core.settings"),
        "core.settings.management_config": _module("core.settings.management_config",
                                                   MANAGEMENT_CONFIG=config if config is not None else {}),
    })


class TesterRightsTest(unittest.TestCase):
    CLOSED = {PERM_KEY_TARIFF, PERM_KEY_DELETE, PERM_KEY_DEVICES}

    def test_tester_closed_by_default(self):
        allowed = action_permissions([], "tester")
        self.assertFalse(self.CLOSED & allowed)
        self.assertIn(PERM_KEY_VIEW, allowed)

    def test_other_roles_unchanged(self):
        for role in ("admin", "support", "staff", "marketer", "designer", ""):
            with self.subTest(role=role):
                self.assertEqual(action_permissions([], role), frozenset(ALL_ACTION_PERMISSIONS))

    def test_configured_rights_win(self):
        allowed = action_permissions([GRANULAR_ACTIONS_CONFIGURED, PERM_KEY_TARIFF], "tester")
        self.assertEqual(allowed, frozenset({PERM_KEY_TARIFF}))

    @unittest.skipIf(CallbackData is None, "aiogram не установлен")
    def test_tester_access_denies_card_actions(self):
        acl = _load_actions()
        tester = acl.AdminActionAccess(False, frozenset(), "tester")
        admin = acl.AdminActionAccess(False, frozenset(), "admin")
        for value in ("admin_user:users_kpu_tag:1:r:0", "admin_user:users_kpu_tg:1:r:0",
                      "admin_user:users_hwid_menu:1:r:0", "admin_user:users_renew:1:r:0",
                      "admin_user:users_delete_key:1:r:0"):
            with self.subTest(value=value):
                need = acl.callback_action_permissions(value)
                self.assertFalse(tester.allows(need))
                self.assertTrue(admin.allows(need))

    @unittest.skipIf(CallbackData is None, "aiogram не установлен")
    def test_screen_callbacks_are_full_admin(self):
        acl = _load_actions()
        for action in ("tst", "tst_ext", "tst_ext_set|1", "tst_int", "tst_int_tg|0", "tst_int_save", "tst_apply"):
            with self.subTest(action=action):
                self.assertEqual(acl.callback_action_permissions(f"admin_panel:{action}:0"), (acl.FULL_ADMIN,))


@unittest.skipIf(CallbackData is None, "aiogram не установлен")
class MiddlewareTest(unittest.TestCase):
    """Без переключателя детальных прав закрытое роли tester всё равно действует."""

    def _run(self, role, value, granular=False, stored=frozenset()):
        acl = _load_actions({"ADMIN_GRANULAR_PERMISSIONS_ENABLED": granular})

        async def get_admin_context(uid, fresh=False):
            return True, role == "superadmin", stored

        async def get_admin_role(uid):
            return role

        mw = _exec("admin_permissions_mw", ROOT / "middlewares/admin_permissions.py", {
            "filters.admin": _module("filters.admin", get_admin_context=get_admin_context,
                                     get_admin_role=get_admin_role),
            "filters.admin_actions": acl,
        })
        answers = []

        class CQ(CallbackQuery):
            async def answer(self, *a, **k):
                answers.append(a)

        event = CQ(id="1", from_user=User(id=5, is_bot=False, first_name="t"), chat_instance="c", data=value)
        reached = []

        async def handler(e, d):
            reached.append(True)

        class BotSession:  # WeakSet в middleware требует слабую ссылку
            def middleware(self, m):
                pass

        bot = types.SimpleNamespace(session=BotSession())
        run(mw.AdminActionPermissionsMiddleware()(handler, event, {"bot": bot}))
        return bool(reached)

    def test_tester_denied_without_switch(self):
        self.assertFalse(self._run("tester", "admin_user:users_kpu_tag:1:r:0"))

    def test_tester_reads_without_switch(self):
        self.assertTrue(self._run("tester", "admin_user:users_key_edit:1:r:0"))

    def test_admin_passes_without_switch(self):
        self.assertTrue(self._run("admin", "admin_user:users_kpu_tag:1:r:0"))

    def test_stored_rights_ignored_without_switch(self):
        # Прежде выключенный переключатель отменял сохранённые детальные права — так и остаётся.
        self.assertFalse(self._run("tester", "admin_user:users_kpu_tag:1:r:0",
                                   stored=frozenset({GRANULAR_ACTIONS_CONFIGURED, PERM_KEY_TARIFF})))
        self.assertTrue(self._run("tester", "admin_user:users_kpu_tag:1:r:0", granular=True,
                                  stored=frozenset({GRANULAR_ACTIONS_CONFIGURED, PERM_KEY_TARIFF})))


if __name__ == "__main__":
    unittest.main()
