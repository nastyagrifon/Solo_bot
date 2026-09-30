"""Загрузчик роутеров по требованию (кассы): импорт только включённых, подключение один раз.

Кассы подставные — временный пакет, бот не поднимается.
Запуск из корня проекта: ``venv/bin/python -m unittest discover -s tests -v``
"""

import sys
import tempfile
import unittest

from pathlib import Path

from aiogram import Router

from core.lazy_routers import LazyRouters


PROVIDER_SRC = "from aiogram import Router\nrouter = Router(name={name!r})\nIMPORTED = True\n"


class LazyRoutersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.pkg = "zz_fake_payments"
        base = root / self.pkg
        base.mkdir()
        (base / "__init__.py").write_text("")
        for name in ("kassai", "heleket", "yookassa"):
            (base / name).mkdir()
            (base / name / "__init__.py").write_text(PROVIDER_SRC.format(name=name))
        sys.path.insert(0, str(root))
        self.parent = Router(name="payments_main_router")
        self.loader = LazyRouters(self.parent, package=self.pkg)
        self.loader.add(".kassai", "KASSAI_CARDS", "KASSAI_SBP")
        self.loader.add(".heleket", "HELEKET")
        self.loader.add(".yookassa", "YOOKASSA")
        self.loader.add(".missing", "MISSING")

    def tearDown(self):
        sys.path.remove(self.tmp.name)
        for m in [m for m in sys.modules if m == self.pkg or m.startswith(self.pkg + ".")]:
            sys.modules.pop(m)
        self.tmp.cleanup()

    def mod(self, name):
        return f"{self.pkg}.{name}"

    def test_boot_imports_only_enabled(self):
        self.assertEqual(self.loader.ensure(["KASSAI_SBP"]), [".kassai"])
        self.assertIn(self.mod("kassai"), sys.modules)
        self.assertNotIn(self.mod("heleket"), sys.modules)
        self.assertNotIn(self.mod("yookassa"), sys.modules)
        self.assertEqual([r.name for r in self.parent.sub_routers], ["kassai"])

    def test_hot_enable_adds_once(self):
        self.loader.ensure([])
        self.assertEqual(self.parent.sub_routers, [])
        self.assertEqual(self.loader.ensure(["HELEKET"]), [".heleket"])
        self.assertEqual(self.loader.ensure(["HELEKET", "KASSAI_CARDS"]), [".kassai"])
        self.assertEqual(self.loader.ensure(["HELEKET", "KASSAI_CARDS"]), [])
        self.assertEqual(sorted(r.name for r in self.parent.sub_routers), ["heleket", "kassai"])
        self.assertEqual(self.loader._loaded, {".heleket", ".kassai"})

    def test_missing_module_warns_once_and_does_not_break_others(self):
        self.assertEqual(self.loader.ensure(["MISSING", "YOOKASSA"]), [".yookassa"])
        self.assertEqual(self.loader.ensure(["MISSING"]), [])
        self.assertNotIn(".missing", self.loader._loaded)

    def test_disabled_flag_never_imports(self):
        self.loader.ensure([])
        self.loader.ensure(["SOMETHING_ELSE"])
        for name in ("kassai", "heleket", "yookassa"):
            self.assertNotIn(self.mod(name), sys.modules)


if __name__ == "__main__":
    unittest.main()
