"""Вызов сводки руками: распознавание команды и выбор адресата.

Обе функции чистые, поэтому грузим файл по пути и подменяем его тяжёлые
зависимости заглушками — база и aiogram для проверки не нужны.
Запуск из корня проекта: ``venv/bin/python -m unittest discover -s tests -v``
"""

import importlib.util
import pathlib
import re
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
    """Исходник одной функции по имени, вместе с `async` если он есть."""
    start = _SOURCE.index(f"def {name}(")
    if _SOURCE[max(0, start - 6):start] == "async ":
        start -= 6
    rest = _SOURCE[start:]
    end = rest.index("\n\n\n")
    return rest[:end]


_ALIASES = tuple(
    re.findall(r'"(/[a-z_-]+)"',
               re.search(r"_REPORT_NOW_ALIASES = \(([^)]*)\)", _SOURCE).group(1))
)

_NS: dict = {"_REPORT_NOW_ALIASES": _ALIASES}
exec(_extract("is_report_now_text"), _NS)          # noqa: S102
exec(_extract("report_now_target"), _NS)           # noqa: S102
is_report_now_text = _NS["is_report_now_text"]
report_now_target = _NS["report_now_target"]


class Recognition(unittest.TestCase):
    def test_both_spellings(self):
        self.assertTrue(is_report_now_text("/report-now"))
        self.assertTrue(is_report_now_text("/report_now"))

    def test_old_names_still_work(self):
        """Прежнее имя не отбираем у тех, кто им пользуется."""
        self.assertTrue(is_report_now_text("/send-now"))
        self.assertTrue(is_report_now_text("/send_now"))

    def test_aliases_taken_from_code(self):
        """Список берётся из кода: иначе тест разойдётся с рабочим."""
        self.assertIn("/report-now", _ALIASES)
        self.assertGreaterEqual(len(_ALIASES), 2)

    def test_case_spaces_and_mention(self):
        for text in (" /SEND-NOW ", "/report-now@some_bot", "/Send_Now"):
            with self.subTest(text=text):
                self.assertTrue(is_report_now_text(text))

    def test_not_a_command(self):
        for text in ("/send", "/report-now позже", "сводка", "", None, "/sendnow"):
            with self.subTest(text=text):
                self.assertFalse(is_report_now_text(text))


class Target(unittest.TestCase):
    def test_private_chat_gets_report_in_place(self):
        chat_id, thread_id, moved = report_now_target("private", 111, 111, 7)
        self.assertEqual((chat_id, thread_id, moved), (111, 7, False))

    def test_group_report_goes_to_dm(self):
        """Выручку в группе видят все, поэтому туда не отправляем."""
        chat_id, thread_id, moved = report_now_target("supergroup", -1001234, 555, 42)
        self.assertEqual(chat_id, 555)
        self.assertIsNone(thread_id)
        self.assertTrue(moved)

    def test_any_non_private_type_goes_to_dm(self):
        for chat_type in ("group", "supergroup", "channel", ""):
            with self.subTest(chat_type=chat_type):
                chat_id, _, moved = report_now_target(chat_type, -42, 999, None)
                self.assertEqual(chat_id, 999)
                self.assertTrue(moved)


class SingleChatDelivery(unittest.TestCase):
    """Отправка в один чат обязана сообщать о неудаче.

    Самый частый сбой - бот не может написать первым в личку тому, кто не
    открывал с ним диалог. Если считать это успехом, человек ищет сводку,
    которой нет.
    """

    def run_send(self, fail: bool):
        import asyncio

        sent = []

        class Bot:
            async def send_message(self, chat_id, text, message_thread_id=None):
                if fail:
                    raise RuntimeError("bot can't initiate conversation")
                sent.append(chat_id)

            async def send_photo(self, *a, **k):
                if fail:
                    raise RuntimeError("bot can't initiate conversation")
                sent.append(a[0] if a else k.get("chat_id"))

        ns = {
            "bot": Bot(),
            "BufferedInputFile": lambda *a, **k: None,
            "select": lambda *a, **k: None,
            "logger": types.SimpleNamespace(
                **{lvl: (lambda *a, **k: None) for lvl in
                   ("debug", "info", "warning", "error", "exception", "critical")}),
            "AsyncSession": object,
            "ADMIN_ID": [],
            "annotations": None,
        }
        exec(_extract("_send_report_to_admins"), ns)                 # noqa: S102
        result = asyncio.run(
            ns["_send_report_to_admins"](None, "текст", None, chat_id=42, thread_id=None)
        )
        return result, sent

    def test_success_returns_true(self):
        ok, sent = self.run_send(fail=False)
        self.assertTrue(ok)
        self.assertEqual(sent, [42])

    def test_failure_returns_false(self):
        ok, sent = self.run_send(fail=True)
        self.assertFalse(ok, "неудачная отправка не должна выглядеть успехом")
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
