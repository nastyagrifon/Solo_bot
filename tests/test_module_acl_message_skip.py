#!/usr/bin/env python3
"""Рубеж модулей не отвечает отказом на текст.

Обработчик модуля с `IsAdminFilter` может ловить свободный текст (поиск по
нику). Запись в `admins` без рабочей роли проходит фильтр, а рубеж отказывал
и отвечал «Этот раздел вам не открыт» — следующие обработчики (умный ответ)
текст так и не получали. Для сообщения рубеж обязан пропустить апдейт дальше,
для нажатия кнопки — по-прежнему показать отказ.

Запуск из корня проекта: ``venv/bin/python -m unittest tests.test_module_acl_message_skip -v``
"""

import asyncio
import importlib.util
import pathlib
import sys
import types
import unittest

from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import CallbackQuery, Chat, Message, User

ROOT = pathlib.Path(__file__).resolve().parent.parent


class _IsAdminFilter:
    pass


def _load_guard_module():
    logger = types.ModuleType("logger")
    logger.logger = types.SimpleNamespace(warning=lambda *a, **k: None)
    admin = types.ModuleType("filters.admin")
    admin.IsAdminFilter = _IsAdminFilter

    async def get_admin_context(user_id, **_):
        return True, False, frozenset()

    async def get_admin_role(user_id):
        return "tester"

    admin.get_admin_context = get_admin_context
    admin.get_admin_role = get_admin_role
    perms = types.ModuleType("filters.permissions")
    perms.MODULE_PERMISSIONS = {}
    perms.WORKING_ROLES = ("superadmin", "admin", "support")
    pkg = types.ModuleType("filters")
    pkg.__path__ = [str(ROOT / "filters")]
    saved = {k: sys.modules.get(k) for k in ("logger", "filters", "filters.admin", "filters.permissions")}
    sys.modules.update({"logger": logger, "filters": pkg, "filters.admin": admin, "filters.permissions": perms})
    try:
        spec = importlib.util.spec_from_file_location("filters.module_acl", ROOT / "filters" / "module_acl.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


ACL = _load_guard_module()
TESTER = User(id=8019752233, is_bot=False, first_name="t")
ADMIN_HANDLER = types.SimpleNamespace(filters=[types.SimpleNamespace(callback=_IsAdminFilter())])


async def _never(event, data):
    raise AssertionError("обработчик модуля не должен вызываться без права")


class GuardOnMessage(unittest.TestCase):
    def test_text_without_right_goes_to_next_handlers(self):
        msg = Message.model_construct(message_id=1, date=0, chat=Chat(id=1, type="private"),
                                      from_user=TESTER, text="@durov")
        guard = ACL.make_guard("admin_text_search")
        with self.assertRaises(SkipHandler):
            asyncio.run(guard(_never, msg, {"handler": ADMIN_HANDLER}))


class GuardOnCallback(unittest.TestCase):
    def test_button_without_right_still_refused(self):
        answers = []

        class Cb(CallbackQuery):
            async def answer(self, text=None, show_alert=None, **_):  # noqa: D401
                answers.append((text, show_alert))

        cb = Cb.model_construct(id="1", from_user=TESTER, chat_instance="x", data="m")
        guard = ACL.make_guard("admin_text_search")
        self.assertIsNone(asyncio.run(guard(_never, cb, {"handler": ADMIN_HANDLER})))
        self.assertEqual(answers, [("Этот раздел вам не открыт", True)])


if __name__ == "__main__":
    unittest.main()
