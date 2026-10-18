"""Вызов сводки руками: распознавание команды и выбор адресата.

Обе функции чистые, поэтому грузим файл по пути и подменяем его тяжёлые
зависимости заглушками — база и aiogram для проверки не нужны.
Запуск из корня проекта: ``venv/bin/python -m unittest discover -s tests -v``
"""

import importlib.util
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "handlers" / "admin" / "stats" / "stats_handler.py"

# Функции короткие и ни от чего не зависят, поэтому берём их исходник из файла
# и исполняем отдельно: поднимать весь обработчик со всем ядром ради двух
# функций дороже, чем вырезать их по имени.
_SOURCE = SRC.read_text(encoding="utf-8")


def _extract(name: str) -> str:
    start = _SOURCE.index(f"def {name}(")
    rest = _SOURCE[start:]
    end = rest.index("\n\n\n")
    return rest[:end]


_NS: dict = {"_SEND_NOW_ALIASES": ("/send-now", "/send_now")}
exec(_extract("is_send_now_text"), _NS)          # noqa: S102
exec(_extract("send_now_target"), _NS)           # noqa: S102
is_send_now_text = _NS["is_send_now_text"]
send_now_target = _NS["send_now_target"]


class Recognition(unittest.TestCase):
    def test_both_spellings(self):
        self.assertTrue(is_send_now_text("/send-now"))
        self.assertTrue(is_send_now_text("/send_now"))

    def test_case_spaces_and_mention(self):
        for text in (" /SEND-NOW ", "/send-now@some_bot", "/Send_Now"):
            with self.subTest(text=text):
                self.assertTrue(is_send_now_text(text))

    def test_not_a_command(self):
        for text in ("/send", "/send-now позже", "сводка", "", None, "/sendnow"):
            with self.subTest(text=text):
                self.assertFalse(is_send_now_text(text))


class Target(unittest.TestCase):
    def test_private_chat_gets_report_in_place(self):
        chat_id, thread_id, moved = send_now_target("private", 111, 111, 7)
        self.assertEqual((chat_id, thread_id, moved), (111, 7, False))

    def test_group_report_goes_to_dm(self):
        """Выручку в группе видят все, поэтому туда не отправляем."""
        chat_id, thread_id, moved = send_now_target("supergroup", -1001234, 555, 42)
        self.assertEqual(chat_id, 555)
        self.assertIsNone(thread_id)
        self.assertTrue(moved)

    def test_any_non_private_type_goes_to_dm(self):
        for chat_type in ("group", "supergroup", "channel", ""):
            with self.subTest(chat_type=chat_type):
                chat_id, _, moved = send_now_target(chat_type, -42, 999, None)
                self.assertEqual(chat_id, 999)
                self.assertTrue(moved)


if __name__ == "__main__":
    unittest.main()
