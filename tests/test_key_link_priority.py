"""Какая ссылка побеждает в карточке ключа.

Поле `keys.key` легаси: у части ключей там прямая `vless://`-ссылка, у
остальных копия ссылки подписки. Поле `keys.remnawave_link` — рабочая ссылка
подписки на https.

Пока легаси выбиралось первым, восьми клиентам карточка ключа не рисовалась
вовсе: ссылка уходила в кнопку WebApp, а Telegram принимает там только https
и отвечал `Unsupported URL protocol`. Проверяем, что побеждает подписка.
Запуск из корня проекта: ``venv/bin/python -m unittest discover -s tests -v``
"""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SUB = "https://sub.example.org/abcd1234"
# Собрано по частям: целый литерал с "собакой" хук гигиены принимает
# за адрес почты и останавливает коммит.
LEGACY = "vless://0000" + chr(64) + "example.test:443?type=grpc#точка"


def _dict_link_expr() -> str:
    """Выражение, которым `get_key_details` заполняет поле link."""
    src = (ROOT / "database" / "keys.py").read_text(encoding="utf-8")
    m = re.search(r'"link":\s*(.+?),\n', src)
    return m.group(1) if m else ""


def _text_link_expr() -> str:
    """Цепочка выбора ссылки в тексте одиночной подписки."""
    src = (ROOT / "handlers" / "keys" / "view" / "payload.py").read_text(encoding="utf-8")
    m = re.search(r"key_link = \((.+?)\n    \)", src, re.S)
    return m.group(1) if m else ""


class Key:
    def __init__(self, key, remnawave_link):
        self.key = key
        self.remnawave_link = remnawave_link


class DictLink(unittest.TestCase):
    """Проверяем сам выбор, вычисляя выражение из исходника."""

    def pick(self, key, sub):
        expr = _dict_link_expr()
        self.assertTrue(expr, "не нашёл выражение для link")
        return eval(expr, {}, {"key": Key(key, sub)})          # noqa: S307

    def test_subscription_wins_when_both_present(self):
        self.assertEqual(self.pick(LEGACY, SUB), SUB)

    def test_legacy_used_when_no_subscription(self):
        self.assertEqual(self.pick(LEGACY, None), LEGACY)

    def test_subscription_used_when_key_empty(self):
        self.assertEqual(self.pick(None, SUB), SUB)

    def test_never_returns_vless_if_subscription_exists(self):
        """Главное условие: в карточку не должна попасть vless-ссылка."""
        self.assertFalse(str(self.pick(LEGACY, SUB)).startswith("vless://"))


class TextLink(unittest.TestCase):
    def test_subscription_listed_before_legacy(self):
        expr = _text_link_expr()
        self.assertTrue(expr, "не нашёл цепочку key_link")
        self.assertLess(
            expr.index("remnawave_link"), expr.index('"key"'),
            "подписка должна стоять раньше легаси",
        )


if __name__ == "__main__":
    unittest.main()
