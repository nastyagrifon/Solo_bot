"""Права на кнопки полей панели и переноса подписки в карточке ключа.

Без записи в разбор прав кнопка проходила бы детальные права насквозь:
пустой набор требований значит «можно всем».
Запуск из корня проекта: ``venv/bin/python -m unittest discover -s tests -v``
"""

import importlib.util
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from filters.permissions import GRANULAR_ACTIONS_CONFIGURED, PERM_KEY_DELETE, PERM_KEY_DEVICES, PERM_KEY_TARIFF  # noqa: E402

try:
    import aiogram  # noqa: F401
except ImportError:
    aiogram = None


def _load():
    stubs = {
        "core": types.ModuleType("core"),
        "core.settings": types.ModuleType("core.settings"),
        "core.settings.management_config": types.SimpleNamespace(MANAGEMENT_CONFIG={}),
    }
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("admin_actions", ROOT / "filters/admin_actions.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return module


@unittest.skipIf(aiogram is None, "aiogram не установлен")
class KeyPanelPermissionsTest(unittest.TestCase):
    def setUp(self):
        self.acl = _load()

    def test_card_buttons(self):
        expected = {
            "users_kpu_ext": PERM_KEY_TARIFF,
            "users_kpu_int": PERM_KEY_TARIFF,
            "users_kpu_tag": PERM_KEY_TARIFF,
            "users_kpu_note": PERM_KEY_TARIFF,
            "users_kpu_tg": PERM_KEY_DELETE,
            "users_hwid_unbind_all": PERM_KEY_DEVICES,
            "users_hwid_unbind_all_ok": PERM_KEY_DEVICES,
        }
        for action, perm in expected.items():
            with self.subTest(action=action):
                value = f"admin_user:{action}:u1:ref:0"
                self.assertEqual(self.acl.callback_action_permissions(value), (perm,))

    def test_screen_buttons(self):
        for action, perm in (("ext_set", PERM_KEY_TARIFF), ("int_tg", PERM_KEY_TARIFF),
                             ("int_save", PERM_KEY_TARIFF), ("clr", PERM_KEY_TARIFF),
                             ("mv_ok", PERM_KEY_DELETE), ("unknown", PERM_KEY_TARIFF)):
            with self.subTest(action=action):
                value = f"kpu:{action}:u1:ref::-1"
                self.assertEqual(self.acl.callback_action_permissions(value), (perm,))

    def test_back_needs_nothing(self):
        self.assertEqual(self.acl.callback_action_permissions("kpu:back:u1:ref::-1"), ())

    def test_text_input_state(self):
        state = "PanelUserState:waiting_value"
        self.assertEqual(self.acl.state_action_permissions(state, {"kpu_field": "tg"}), (PERM_KEY_DELETE,))
        self.assertEqual(self.acl.state_action_permissions(state, {"kpu_field": "tag"}), (PERM_KEY_TARIFF,))

    def test_transfer_hidden_without_delete_right(self):
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="tag", callback_data="admin_user:users_kpu_tag:u1:ref:0"),
            InlineKeyboardButton(text="tg", callback_data="admin_user:users_kpu_tg:u1:ref:0"),
        ]])
        access = self.acl.AdminActionAccess(False, frozenset({GRANULAR_ACTIONS_CONFIGURED, PERM_KEY_TARIFF}))
        left = [b.text for row in self.acl.filter_admin_markup(markup, access).inline_keyboard for b in row]
        self.assertEqual(left, ["tag"])


if __name__ == "__main__":
    unittest.main()
