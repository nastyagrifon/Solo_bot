"""Права админки: роли, пресеты и рубеж правки в карточке клиента.

Карточка целиком висела под одним правом `users`, поэтому «смотреть» и
«править» не различались, а суперадмином считалась любая роль, кроме
`moderator` — опечатка в поле открывала полный доступ.

Отдельно страхуем связь по имени: рубеж правки знает читающие обработчики по
именам функций, и если имя в ядре переименуют, список обязан покраснеть тут,
а не тихо отобрать у сотрудника просмотр.

Запуск из корня проекта: ``venv/bin/python -m unittest discover -s tests -v``
"""

import ast
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from filters.permissions import (  # noqa: E402
    ALL_PERMISSIONS,
    ALL_ROLES,
    PERM_BALANCE,
    PERM_USERS,
    PERM_USERS_WRITE,
    ROLE_ALIASES,
    ROLE_PRESETS,
    SUPERADMIN_ROLE,
    normalize_role,
    permissions_for_role,
)


def handler_names() -> set[str]:
    """Имена зарегистрированных обработчиков карточки клиента."""
    found = set()
    # Рекурсивно по всему дереву: перечисление подкаталогов вручную врало бы
    # при появлении нового подкаталога.
    for f in (ROOT / "handlers/admin/users").rglob("*.py"):
        for node in ast.parse(f.read_text(encoding="utf-8")).body:
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            decorators = " ".join(ast.unparse(d) for d in node.decorator_list)
            if "router." in decorators:
                found.add(node.name)
    return found


class Roles(unittest.TestCase):
    def test_superadmin_is_single_role(self):
        self.assertEqual(SUPERADMIN_ROLE, "superadmin")

    def test_typo_in_role_is_not_superadmin(self):
        for typo in ("supperadmin", "superadmim", "админ", "", None, "tester2"):
            self.assertNotEqual(normalize_role(typo), SUPERADMIN_ROLE, typo)

    def test_unknown_role_gets_least_rights(self):
        self.assertEqual(normalize_role("whatever"), "staff")
        self.assertNotIn(PERM_USERS_WRITE, permissions_for_role("whatever"))

    def test_legacy_moderator_becomes_support(self):
        self.assertEqual(normalize_role("moderator"), "support")
        self.assertEqual(ROLE_ALIASES["moderator"], "support")

    def test_case_and_spaces_are_tolerated(self):
        self.assertEqual(normalize_role("  SuperAdmin "), SUPERADMIN_ROLE)

    def test_marketer_runs_promos_without_client_edits(self):
        from filters.permissions import (PERM_ADS, PERM_BROADCASTING,
                                         PERM_COUPONS, PERM_GIFTS)
        perms = permissions_for_role("marketer")
        for perm in (PERM_COUPONS, PERM_GIFTS, PERM_ADS, PERM_BROADCASTING):
            self.assertIn(perm, perms, perm)
        self.assertIn(PERM_USERS, perms)
        self.assertNotIn(PERM_USERS_WRITE, perms)
        self.assertNotIn(PERM_BALANCE, perms)

    def test_roles_present(self):
        for role in ("superadmin", "admin", "support", "tester", "marketer",
                     "staff", "designer"):
            self.assertIn(role, ALL_ROLES)

    def test_support_does_financial_operations(self):
        perms = permissions_for_role("support")
        self.assertIn(PERM_BALANCE, perms)
        self.assertIn(PERM_USERS_WRITE, perms)

    def test_tester_edits_but_no_money(self):
        perms = permissions_for_role("tester")
        self.assertIn(PERM_USERS_WRITE, perms)
        self.assertNotIn(PERM_BALANCE, perms)

    def test_staff_only_reads(self):
        perms = permissions_for_role("staff")
        self.assertIn(PERM_USERS, perms)
        self.assertNotIn(PERM_USERS_WRITE, perms)
        self.assertNotIn(PERM_BALANCE, perms)

    def test_presets_reference_existing_permissions(self):
        for role, perms in ROLE_PRESETS.items():
            for perm in perms:
                self.assertIn(perm, ALL_PERMISSIONS, f"{role}: {perm}")


class MigrationSnapshot(unittest.TestCase):
    """Перенос v57 обязан быть снимком, а не ссылкой на прикладной список."""

    def setUp(self):
        self.src = (ROOT / "database/migrations/schema_upgrade.py").read_text(
            encoding="utf-8")

    def test_migration_does_not_import_app_code(self):
        # Иначе один и тот же перенос выдаст разные права на разных
        # установках, стоит поменять ROLE_PRESETS.
        self.assertNotIn("from filters", self.src)

    def test_snapshot_matches_current_presets(self):
        """Снимок и живой список разошлись — значит нужен новый перенос."""
        block = re.search(r"presets = \{(.+?)\n    \}", self.src, re.S)
        self.assertIsNotNone(block, "в переносе нет снимка наборов")
        snapshot = dict(re.findall(r'"([a-z]+)": \[([^\]]*)\]', block.group(1)))
        self.assertEqual(set(snapshot), set(ROLE_PRESETS),
                         "роли в снимке и в коде разошлись")
        for role, raw in snapshot.items():
            got = tuple(re.findall(r'"([a-z_]+)"', raw))
            self.assertEqual(got, tuple(ROLE_PRESETS[role]),
                             f"набор роли {role} разошёлся со снимком переноса")

    def test_snapshot_keeps_legacy_rename(self):
        self.assertIn('renames = {"moderator": "support"}', self.src)


class EditBoundary(unittest.TestCase):
    def setUp(self):
        # Грузим файлом, а не через пакет: `handlers/__init__.py` тянет
        # aiogram, а правила прав обязаны проверяться без установленного бота.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "users_acl_module", ROOT / "handlers/admin/users/acl.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.acl = module

    def test_money_module_requires_balance(self):
        need = self.acl.required_permission(
            "handlers.admin.users.users_balance", "handle_balance_input")
        self.assertEqual(need, PERM_BALANCE)

    def test_money_history_is_read_only(self):
        for name in ("handle_balance_page", "handle_balance_export"):
            self.assertIsNone(self.acl.required_permission(
                "handlers.admin.users.users_balance", name), name)

    def test_opening_take_screen_needs_balance(self):
        # Кто не вправе списывать, не должен и открывать экран списания.
        self.assertEqual(self.acl.required_permission(
            "handlers.admin.users.users_balance", "handle_balance_take"), PERM_BALANCE)

    def test_unknown_handler_requires_write(self):
        # Обработчик, появившийся в ядре после нас, требует больше прав, а не
        # меньше.
        self.assertEqual(self.acl.required_permission(
            "handlers.admin.users.users_keys.lifecycle", "handle_brand_new_thing"),
            PERM_USERS_WRITE)

    def test_known_write_handlers_require_write(self):
        for name in ("handle_hwid_unbind", "handle_delete_key_confirm",
                     "handle_user_ban", "handle_reset_traffic", "handle_cfg_save",
                     "handle_unlink_tg", "handle_send_user_message"):
            self.assertEqual(self.acl.required_permission(
                "handlers.admin.users.whatever", name), PERM_USERS_WRITE, name)

    def test_read_only_handlers_need_nothing_extra(self):
        for name in ("handle_user_audit", "handle_search_user", "handle_hwid_menu",
                     "handle_user_traffic", "handle_hosts_list"):
            self.assertIsNone(self.acl.required_permission(
                "handlers.admin.users.whatever", name), name)

    def test_listed_names_exist_in_code(self):
        """Переименовали обработчик в ядре — список обязан покраснеть здесь."""
        existing = handler_names()
        self.assertTrue(existing, "обработчики карточки не найдены вовсе")
        for name in self.acl.READ_ONLY | self.acl.MONEY_READ_ONLY:
            self.assertIn(name, existing, f"{name} больше нет в коде")

    def test_every_role_has_a_title(self):
        """Роль без подписи не появится на экране выдачи."""
        src = (ROOT / "handlers/admin/management/keyboard.py").read_text(encoding="utf-8")
        for role in ALL_ROLES:
            self.assertIn(f'"{role}":', src, f"{role} нет в ROLE_TITLES")

    def test_menu_trusts_context_about_superadmin(self):
        """Меню не должно решать само: админ из ADMIN_ID супер без прав."""
        src = (ROOT / "handlers/admin/panel/keyboard.py").read_text(encoding="utf-8")
        self.assertIn("is_superadmin: bool | None = None", src)
        self.assertIn(
            'is_super = (admin_role == "superadmin") if is_superadmin is None '
            "else is_superadmin", src)
        caller = (ROOT / "handlers/admin/panel/panel_handler.py").read_text(encoding="utf-8")
        self.assertEqual(caller.count("is_superadmin=is_super"), 2,
                         "оба вызова меню обязаны передавать признак из контекста")

    def test_middleware_installed_on_nested_routers(self):
        """Рубеж вешается на дерево: у роутера карточки своих обработчиков нет."""
        src = (ROOT / "handlers/admin/users/__init__.py").read_text(encoding="utf-8")
        self.assertIn("_install_acl(router)", src)
        self.assertGreater(src.index("_install_acl(router)"),
                           src.rindex("router.include_router("),
                           "ставить рубеж надо после include_router")


if __name__ == "__main__":
    unittest.main(verbosity=2)
