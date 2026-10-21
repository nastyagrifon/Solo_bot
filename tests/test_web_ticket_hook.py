#!/usr/bin/env python3
"""Ядро отдаёт обращение из кабинета подписчикам.

Модуль поддержки держит топики на КЛИЕНТА, с автосообщениями и карточками.
Заводить рядом второй топик на обращение значило бы вести одного человека в
двух местах, поэтому обработчик может забрать доставку себе.

Запуск из корня проекта: ``venv/bin/python -m unittest discover -s tests -v``
"""

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = (ROOT / "services" / "tickets" / "delivery.py").read_text(encoding="utf-8")


class Hook(unittest.TestCase):
    def test_hook_is_called_for_new_ticket(self):
        self.assertIn('run_hooks("web_ticket_created"', SRC)

    def test_hook_gets_session_and_ticket(self):
        self.assertIn("session=session, ticket=ticket", SRC)

    def test_core_stays_silent_when_taken(self):
        """True от обработчика — доставку забрали, второй раз не доставляем."""
        self.assertIn("if await _taken_by_hook(session, ticket):\n        return", SRC)

    def test_hook_runs_before_forum_is_touched(self):
        """Иначе ядро успеет завести свой топик на обращение.

        Сверяем с импортом форума, а не с ветвлением: перенос вызова на
        строку перед `if forum_enabled()` сохраняет порядок текста, но ядро
        уже подтянуло механику топиков.
        """
        self.assertLess(SRC.index("_taken_by_hook(session, ticket)"),
                        SRC.index("from .forum import ensure_topic, forum_enabled"))

    def test_broken_hook_does_not_break_delivery(self):
        """Модуль может быть выключен или упасть — ядро доставляет само."""
        self.assertIn("except Exception as e:", SRC)
        self.assertIn("хук web_ticket_created упал", SRC)
        block = SRC[SRC.index("async def _taken_by_hook"):SRC.index("async def notify_agents_new_ticket")]
        self.assertIn("return False", block)

    def test_only_true_counts_as_taken(self):
        """Обработчик, вернувший None, доставку не забирает."""
        self.assertIn("any(r is True for r in", SRC)


if __name__ == "__main__":
    unittest.main(verbosity=2)
