"""Поля юзера панели в карточке ключа: разбор ввода админа.

Запуск из корня проекта: ``venv/bin/python -m unittest discover -s tests -v``
"""

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "panel_user_validate", ROOT / "handlers/admin/users/panel_user/validate.py"
)
validate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate)


class TagTest(unittest.TestCase):
    def test_upper_and_allowed(self):
        self.assertEqual(validate.parse_tag(" vip_2 "), "VIP_2")

    def test_clear(self):
        self.assertIsNone(validate.parse_tag("-"))

    def test_rejects_bad(self):
        self.assertEqual(validate.parse_tag("A" * 16), "A" * 16)
        for raw in ("", "A" * 17, "TOO_LONG_TAG_12345", "ТЕГ", "A-B", "A B"):
            with self.subTest(raw=raw), self.assertRaises(validate.InputError):
                validate.parse_tag(raw)


class TelegramIdTest(unittest.TestCase):
    def test_number(self):
        self.assertEqual(validate.parse_telegram_id(" 12345 "), 12345)

    def test_rejects_bad(self):
        # «-» больше не очищает: TG ID задаёт получателя переноса, пустого получателя нет.
        for raw in ("-", "", "0", "-5", "12a", "1.5", "²", "١٢٣", "9" * 16):
            with self.subTest(raw=raw), self.assertRaises(validate.InputError):
                validate.parse_telegram_id(raw)


class DescriptionTest(unittest.TestCase):
    def test_text(self):
        self.assertEqual(validate.parse_description("  платит наличными "), "платит наличными")

    def test_clear(self):
        self.assertIsNone(validate.parse_description("-"))
        self.assertIsNone(validate.parse_description("  "))

    def test_too_long(self):
        with self.assertRaises(validate.InputError):
            validate.parse_description("x" * 1001)


if __name__ == "__main__":
    unittest.main()
