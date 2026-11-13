#!/usr/bin/env python3
"""Отказ при занятой почте не выдаёт, что адрес есть в базе.

Различимый ответ («этот email уже занят») позволяет перебором узнать, чьи
адреса зарегистрированы. И бот, и регистрация на сайте отвечают одним
общим отказом со ссылкой в поддержку.

Запуск из корня проекта: ``venv/bin/python -m unittest tests.test_email_refusal_generic -v``
"""

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BOT = (ROOT / "handlers" / "email_binding.py").read_text(encoding="utf-8")
WEB = (ROOT / "api" / "v2" / "routes" / "auth" / "password.py").read_text(encoding="utf-8")


class Refusal(unittest.TestCase):
    def test_bot_never_says_taken(self):
        self.assertNotIn("уже занят", BOT)

    def test_bot_uses_one_refusal_everywhere(self):
        self.assertEqual(BOT.count("REFUSAL_TEXT, reply_markup="), 3)

    def test_with_code_taken_check_waits_for_code(self):
        """С подтверждением кодом занятость не проверяется до письма."""
        head = BOT[BOT.index("async def receive_email("):BOT.index("async def receive_email_code(")]
        gate = head.index("if not verification_required:")
        self.assertGreater(head.index("get_identity_by_email("), gate)

    def test_web_registration_never_says_exists(self):
        self.assertNotIn("уже существует", WEB)
        self.assertIn("Напишите в поддержку", WEB)


if __name__ == "__main__":
    unittest.main()
