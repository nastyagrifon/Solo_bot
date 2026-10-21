#!/usr/bin/env python3
"""Рубеж админских экранов модулей.

Модули проверяют только факт записи в `admins`. С появлением ролей это стало
дырой: сотрудник с доступом «только смотреть» открывал настройки модулей,
рассылки и менеджер модулей. Рубеж стоит в загрузчике — одно место на все
модули.

Клиентские экраны модулей трогать нельзя: в одном роутере и админские, и
клиентские обработчики. Админским считаем тот, на который модуль сам повесил
`IsAdminFilter`.

Запуск из корня проекта: ``venv/bin/python -m unittest discover -s tests -v``
"""

import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
GUARD = (ROOT / "filters" / "module_acl.py").read_text(encoding="utf-8")
LOADER = (ROOT / "utils" / "modules_loader.py").read_text(encoding="utf-8")
PERMS = (ROOT / "filters" / "permissions.py").read_text(encoding="utf-8")
ADMIN = (ROOT / "filters" / "admin.py").read_text(encoding="utf-8")


class Wiring(unittest.TestCase):
    def test_loader_installs_guard_after_include(self):
        """Ставить рубеж надо после include_router: иначе дерево ещё пусто."""
        self.assertIn("module_acl.install(router, name)", LOADER)
        self.assertGreater(LOADER.index("module_acl.install(router, name)"),
                           LOADER.index("modules_hub.include_router(router)"))

    def test_guard_walks_nested_routers(self):
        # Inner middleware действует только на свои обработчики.
        self.assertIn("for sub in router.sub_routers:", GUARD)
        self.assertIn("install(sub, module_name)", GUARD)

    def test_guard_hangs_on_both_event_types(self):
        self.assertIn("router.callback_query.middleware(guard)", GUARD)
        self.assertIn("router.message.middleware(guard)", GUARD)


class Rules(unittest.TestCase):
    def test_client_screens_pass_through(self):
        """Обработчик без IsAdminFilter — клиентский, его не трогаем."""
        self.assertIn("if not _is_admin_handler(data.get(\"handler\")):", GUARD)
        self.assertIn("return await handler(event, data)", GUARD)

    def test_admin_screen_detected_by_module_own_filter(self):
        self.assertIn("isinstance(getattr(item, \"callback\", None), IsAdminFilter)",
                      GUARD)

    def test_superadmin_passes(self):
        self.assertIn("if is_super:", GUARD)

    def test_mapped_module_needs_its_permission(self):
        self.assertIn("allowed = need in perms", GUARD)

    def test_unmapped_module_needs_working_role(self):
        self.assertIn("await get_admin_role(user.id) in WORKING_ROLES", GUARD)

    def test_refusal_is_not_silent(self):
        """Немой отказ выглядит сломанной кнопкой."""
        self.assertIn("show_alert=True", GUARD)

    def test_no_second_database_trip(self):
        """Роль берётся из кэша прав, а не отдельным запросом."""
        self.assertNotIn("async_session_maker", GUARD)
        self.assertNotIn("select(Admin", GUARD)


class RoleFromCache(unittest.TestCase):
    def test_role_lives_in_the_same_cache(self):
        self.assertIn("async def get_admin_role(user_id: int) -> str:", ADMIN)
        self.assertIn("return (await _resolve_admin(user_id))[3]", ADMIN)

    def test_old_context_contract_kept(self):
        """Прежние три значения на месте: вызовы ядра не переписывались."""
        self.assertIn("return is_admin, is_super, perms", ADMIN)

    def test_every_resolve_call_unpacks_four(self):
        tree = ast.parse(ADMIN)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            src = ast.unparse(node)
            if "_resolve_admin(" not in src or "[3]" in src:
                continue
            target = node.targets[0]
            if isinstance(target, ast.Tuple):
                self.assertEqual(len(target.elts), 4, src)


class Map(unittest.TestCase):
    def test_working_roles_are_the_client_facing_ones(self):
        self.assertIn('WORKING_ROLES: tuple[str, ...] = ("superadmin", "admin", "support")',
                      PERMS)

    def test_map_is_not_blind(self):
        """Карта вслепую на все модули закрыла бы доступ работающим людям."""
        import re
        block = re.search(r"MODULE_PERMISSIONS: dict\[str, str\] = \{(.+?)\n\}",
                          PERMS, re.S)
        self.assertIsNotNone(block, "карты прав модулей нет")
        pairs = re.findall(r'"([A-Za-z_]+)": (PERM_[A-Z_]+)', block.group(1))
        self.assertTrue(pairs, "карта пуста")
        self.assertLessEqual(len(pairs), 8)
        for module, perm in pairs:
            self.assertIn(f"{perm} = ", PERMS, f"{module}: {perm} не объявлено")


if __name__ == "__main__":
    unittest.main(verbosity=2)
