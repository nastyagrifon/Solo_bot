"""Покупка ключа списывает деньги до создания в панели.

Раньше ``key_cluster_mode`` и ``create_vpn_key_headless`` создавали клиента в
панели и запись ключа, а списывали в самом конце. Отказ списания или любой
сбой после панели ловился в ``except Exception`` без отката: middleware сессии
коммитило ключ бесплатно, а в API-пути откат базы оставлял рабочего клиента в
панели без записи.

Проверяем по исходнику: списание стоит раньше создания в панели; обработчик
при сбое откатывает сессию и удаляет созданного клиента из панели; headless-путь
тоже удаляет клиента при сбое после панели.

Тест читает только исходник, база и aiogram не нужны.
Запуск из корня проекта: ``venv/bin/python -m unittest tests.test_cluster_buy_charge_first -v``
"""

import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _func(path, name):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)


def _first_call_line(node, name):
    lines = [c.lineno for c in ast.walk(node) if isinstance(c, ast.Call) and ast.unparse(c.func) == name]
    return min(lines) if lines else None


def _calls(node):
    return {ast.unparse(c.func) for c in ast.walk(node) if isinstance(c, ast.Call)}


class ClusterBuyChargeFirst(unittest.TestCase):
    def test_handler_debits_before_panel(self):
        fn = _func("handlers/keys/key_mode/key_cluster_mode.py", "key_cluster_mode")
        self.assertLess(_first_call_line(fn, "update_balance"), _first_call_line(fn, "create_key_on_cluster"))

    def test_handler_failure_rolls_back_and_removes_panel_client(self):
        fn = _func("handlers/keys/key_mode/key_cluster_mode.py", "key_cluster_mode")
        handlers = [h for h in ast.walk(fn) if isinstance(h, ast.ExceptHandler)
                    and isinstance(h.type, ast.Name) and h.type.id == "Exception"
                    and any(isinstance(c, ast.Call) and ast.unparse(c.func) == "logger.error"
                            and "Ошибка при создании ключа" in ast.unparse(c) for c in ast.walk(h))]
        self.assertEqual(len(handlers), 1)
        calls = _calls(handlers[0])
        self.assertIn("session.rollback", calls)
        self.assertIn("delete_key_from_cluster", calls)

    def test_headless_debits_before_panel_and_cleans_up(self):
        fn = _func("services/keys.py", "create_vpn_key_headless")
        self.assertLess(_first_call_line(fn, "update_balance"), _first_call_line(fn, "create_key_on_cluster"))
        self.assertGreater(_first_call_line(fn, "delete_key_from_cluster") or 0,
                           _first_call_line(fn, "create_key_on_cluster"))


if __name__ == "__main__":
    unittest.main()
