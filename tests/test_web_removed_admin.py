#!/usr/bin/env python3
"""Снятый в боте админ теряет и админку сайта.

Прежде при отсутствии строки в `admins` сайт брал флаг `Identity.is_admin`,
а удаление админа в боте этот флаг и открытые сессии не трогало: снятый
админ продолжал работать в веб-кабинете. Флаг без Telegram (веб-админ из
WEB_ADMIN_LOGIN) обязан работать как прежде.

Запуск из корня проекта: ``venv/bin/python -m unittest tests.test_web_removed_admin -v``
"""

import ast
import asyncio
import importlib.util
import pathlib
import sys
import types
import unittest

from fastapi import HTTPException

ROOT = pathlib.Path(__file__).resolve().parent.parent
ROWS: dict[int, object] = {}


class _Col:
    def __eq__(self, other):
        return other


class _Admin:
    tg_id = _Col()


class _Any:
    pass


def _mod(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


async def _noop(*a, **k):
    return None


def _load_depends():
    stubs = {
        "api.admin_permissions": _mod(
            "api.admin_permissions", require_admin_action=_noop, require_legacy_resource_permission=_noop
        ),
        "audit": _mod("audit", set_api_actor=lambda *a, **k: None),
        "core.redis_cache": _mod("core.redis_cache", cache_get=_noop, cache_key=lambda *a: "k", cache_set=_noop),
        "core.settings.runtime_sync": _mod("core.settings.runtime_sync", maybe_sync_runtime_configs=_noop),
        "database": _mod("database", async_session_maker=None, identities=_Any(), identity_sessions=_Any()),
        "database.access.resolution": _mod(
            "database.access.resolution",
            ActorSurface=_Any,
            ResolvedActor=_Any,
            get_user_by_id=_noop,
            public_tg_id=lambda *a: None,
            resolve_actor_from_identity=_noop,
            telegram_chat_id=lambda *a: None,
        ),
        "database.models": _mod("database.models", Admin=_Admin, Identity=_Any),
        "database.site_state": _mod("database.site_state", mark_site_initialized=_noop),
        "filters.admin_actions": _mod("filters.admin_actions", granular_permissions_enabled=lambda: False),
        "logger": _mod("logger", logger=types.SimpleNamespace(debug=lambda *a, **k: None)),
        "settings.cache_config": _mod("settings.cache_config", AUTH_ACTOR_CACHE_TTL_SEC=60),
    }
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("api_depends", ROOT / "api" / "depends.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    mod.select = lambda _model: types.SimpleNamespace(where=lambda tg: tg)
    # site_state импортируется внутри функции, держим заглушку на время теста.
    sys.modules["database.site_state"] = stubs["database.site_state"]
    return mod


DEP = _load_depends()


class _Session:
    async def execute(self, tg_id):
        return types.SimpleNamespace(scalar_one_or_none=lambda: ROWS.get(tg_id))


def _role(identity):
    return asyncio.run(DEP.resolve_identity_role(_Session(), identity))


class RemovedAdmin(unittest.TestCase):
    def test_removed_tg_admin_is_user(self):
        ROWS.clear()
        removed = types.SimpleNamespace(id="i1", tg_id=555, is_admin=True)
        self.assertEqual(_role(removed), "user")

        async def cookie(session, request):
            return removed

        DEP._identity_from_cookie = cookie
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(DEP.verify_identity_admin(None, _Session()))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_current_admin_and_web_bootstrap_admin_keep_access(self):
        ROWS.clear()
        ROWS[777] = types.SimpleNamespace(role="superadmin")
        self.assertEqual(_role(types.SimpleNamespace(tg_id=777, is_admin=True)), "admin")
        self.assertEqual(_role(types.SimpleNamespace(tg_id=None, is_admin=True)), "admin")
        self.assertEqual(_role(types.SimpleNamespace(tg_id=None, is_admin=False)), "user")

    def test_delete_admin_drops_web_flag_and_sessions(self):
        tree = ast.parse((ROOT / "handlers" / "admin" / "management" / "admins.py").read_text())
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "delete_admin")
        resets = [
            n
            for n in ast.walk(fn)
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Attribute) and t.attr == "is_admin" for t in n.targets)
            and isinstance(n.value, ast.Constant)
            and n.value.value is False
        ]
        calls = {n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertTrue(resets, "delete_admin не сбрасывает Identity.is_admin")
        self.assertIn("delete_other_sessions", calls)


if __name__ == "__main__":
    unittest.main()
