#!/usr/bin/env python3
"""Сайт считает суперадмином только роль `superadmin`, как бот.

Прежде веб считал суперадмином любую роль, кроме `moderator`: `support`,
`staff`, `tester` получали на сайте полный доступ — правку баланса, удаление
клиентов, возвраты, — которого в боте у них нет.

Запуск из корня проекта: ``venv/bin/python -m unittest tests.test_web_admin_role_super -v``
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


class _Admin:
    pass


def _load_module():
    mgmt = types.ModuleType("core.settings.management_config")
    mgmt.MANAGEMENT_SETTING_KEY = "MANAGEMENT"
    mgmt.MANAGEMENT_CONFIG = {"ADMIN_GRANULAR_PERMISSIONS_ENABLED": True}
    admins = types.ModuleType("database.admins")

    async def get_admin_by_tg_id(session, tg_id):
        return ROWS.get(tg_id)

    admins.get_admin_by_tg_id = get_admin_by_tg_id
    models = types.ModuleType("database.models")
    models.Admin = _Admin
    config = types.ModuleType("settings.config")
    config.ADMIN_ID = (1,)
    filters = types.ModuleType("filters")
    filters.__path__ = [str(ROOT / "filters")]
    stubs = {
        "core.settings.management_config": mgmt,
        "database.admins": admins,
        "database.models": models,
        "settings.config": config,
        "filters": filters,
    }
    names = (*stubs, "filters.permissions", "filters.admin_actions")
    saved = {k: sys.modules.get(k) for k in names}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("api_admin_permissions", ROOT / "api" / "admin_permissions.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


AP = _load_module()


def _is_full(role: str) -> bool:
    ROWS[100] = types.SimpleNamespace(role=role, permissions=["users", "stats"])
    principal = types.SimpleNamespace(tg_id=100, is_admin=True)
    try:
        asyncio.run(AP.require_full_admin(None, principal))
    except HTTPException as exc:
        assert exc.status_code == 403
        return False
    return True


class WebSuperadminRole(unittest.TestCase):
    def test_bot_roles_are_not_superadmin(self):
        for role in ("support", "staff", "tester", "marketer", "admin", "moderator", "typo-role"):
            with self.subTest(role=role):
                self.assertFalse(_is_full(role))

    def test_superadmin_keeps_full_access(self):
        self.assertTrue(_is_full("superadmin"))
        self.assertTrue(_is_full(" SuperAdmin "))

    def test_designer_still_denied(self):
        self.assertFalse(_is_full("designer"))


if __name__ == "__main__":
    unittest.main()
