#!/usr/bin/env python3
"""Короткая проверка админа отказывает дизайнеру и модератору всегда.

Прежде проверка роли в `verify_identity_admin_short` стояла внутри
`if granular_permissions_enabled()`: при выключенных детальных правах
дизайнер и модератор запускали рассылку и отправку статистики.

Запуск из корня проекта: ``venv/bin/python -m unittest tests.test_web_admin_short_role -v``
"""

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


class _Session:
    async def execute(self, tg_id):
        return types.SimpleNamespace(scalar_one_or_none=lambda: ROWS.get(tg_id))

    async def commit(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


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
        "database": _mod("database", async_session_maker=_Session, identities=_Any(), identity_sessions=_Any()),
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
        # Детальные права выключены — ровно тот случай, где проверка роли пропадала.
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
    return mod


DEP = _load_depends()


def _check(role):
    ROWS.clear()
    ROWS[42] = types.SimpleNamespace(role=role)
    identity = types.SimpleNamespace(id="i42", tg_id=42, is_admin=True)

    async def cookie(session, request):
        return identity

    DEP._identity_from_cookie = cookie
    return asyncio.run(DEP.verify_identity_admin_short(None))


class ShortAdminRole(unittest.TestCase):
    def test_designer_and_moderator_denied_without_granular(self):
        for role in ("designer", "moderator"):
            with self.subTest(role=role):
                with self.assertRaises(HTTPException) as ctx:
                    _check(role)
                self.assertEqual(ctx.exception.status_code, 403)

    def test_admin_passes(self):
        self.assertEqual(_check("superadmin").tg_id, 42)


if __name__ == "__main__":
    unittest.main()
