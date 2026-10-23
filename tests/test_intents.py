#!/usr/bin/env python3
"""Кто владеет доставкой обращений: ядро или модуль.

Прежде ядро спрашивало хук и смотрело, вернул ли тот `True`. Владение
решалось на каждом событии, молчащий обработчик было не отличить от
отсутствующего, и ядро зря собирало переписку с вложениями. Теперь модуль
объявляет намерение один раз при загрузке.

Запуск из корня проекта: ``venv/bin/python -m unittest discover -s tests -v``
"""

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
# Реестру безразлично, кто заявил намерение, поэтому заявители здесь условные.
OWNER = "module_a"
OTHER = "module_b"
DELIVERY = (ROOT / "services" / "tickets" / "delivery.py").read_text(encoding="utf-8")


def load_intents():
    """Грузим файлом: пакет ядра тянет настройки и базу."""
    spec = importlib.util.spec_from_file_location(
        "intents_under_test", ROOT / "core" / "intents.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Registry(unittest.TestCase):
    def setUp(self):
        self.intents = load_intents()
        self.name = self.intents.TICKETS_DELIVERY

    def test_free_by_default(self):
        """Ничего не заявлено — доставляет ядро."""
        self.assertIsNone(self.intents.owner_of(self.name))
        self.assertFalse(self.intents.taken(self.name))

    def test_claim_makes_owner(self):
        self.assertTrue(self.intents.claim(self.name, OWNER))
        self.assertEqual(self.intents.owner_of(self.name), OWNER)
        self.assertTrue(self.intents.taken(self.name))

    def test_second_claimant_is_refused(self):
        """Два модуля с одним намерением — ошибка настройки, а не гонка."""
        self.intents.claim(self.name, OWNER)
        self.assertFalse(self.intents.claim(self.name, OTHER))
        self.assertEqual(self.intents.owner_of(self.name), OWNER)

    def test_same_owner_can_reclaim(self):
        """Повторная загрузка модуля не должна ломать владение."""
        self.intents.claim(self.name, OWNER)
        self.assertTrue(self.intents.claim(self.name, OWNER))

    def test_unknown_intent_is_refused(self):
        self.assertFalse(self.intents.claim("что-то.левое", OWNER))

    def test_release_only_by_owner(self):
        self.intents.claim(self.name, OWNER)
        self.assertFalse(self.intents.release(self.name, OTHER))
        self.assertTrue(self.intents.release(self.name, OWNER))
        self.assertIsNone(self.intents.owner_of(self.name))


class CoreDelivery(unittest.TestCase):
    def test_core_asks_owner_not_hook_result(self):
        self.assertIn("owner = owner_of(TICKETS_DELIVERY)", DELIVERY)
        self.assertIn("if not owner:\n        return False", DELIVERY)

    def test_core_delivers_itself_when_owner_fails(self):
        """Упавший владелец не должен оставлять оператора без обращения."""
        self.assertIn("не взял обращение", DELIVERY)
        block = DELIVERY[DELIVERY.index("async def _taken_by_owner"):
                         DELIVERY.index("async def notify_agents_new_ticket")]
        self.assertIn("return False", block)

    def test_owner_checked_before_forum_is_touched(self):
        """Иначе ядро успеет подтянуть свою механику топиков."""
        self.assertLess(DELIVERY.index("_taken_by_owner(session, ticket)"),
                        DELIVERY.index("from .forum import ensure_topic, forum_enabled"))

    def test_hook_still_carries_the_event(self):
        """Владение объявляется заранее, само событие по-прежнему хуком."""
        self.assertIn('run_hooks("web_ticket_created"', DELIVERY)


if __name__ == "__main__":
    unittest.main(verbosity=2)
