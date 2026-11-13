"""Остальные проверки «хватает ли денег» тоже идут по точному балансу.

``get_balance`` без ``exact=True`` отдаёт баланс, округлённый до десятых
(и из такого же кеша), — это для показа. Продление, покупка, триал, подарок,
доплата за опции, автопродление и чекаут сравнивали с ним цену или считали
от него сумму доплаты, а ``update_balance`` списывает по точному значению:
баланс 99.97 при цене 100 проходил проверку, и отказ всплывал уже при
списании (или доплата выходила нулевой).

Проверяем по исходникам (AST): в каждой такой функции значение ``balance``,
по которому принимается решение, читается ``get_balance(..., exact=True)``.
Показ (``new_balance``, ``balance_rub`` и т. п.) не трогается. Модули не
импортируются: обработчики тянут бота целиком.
Запуск из корня проекта: ``venv/bin/python -m unittest tests.test_balance_exact_rest -v``
"""

import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

DECISIONS = (
    ("handlers/keys/renew/router.py", "handle_renew_config_confirm"),
    ("handlers/keys/create/flow.py", "handle_key_creation"),
    ("handlers/notifications/renewal.py", "try_auto_renew"),
    ("api/v2/routes/tariffs.py", "purchase_tariff_with_balance"),
    ("api/v2/routes/tariffs.py", "activate_trial"),
    ("api/v2/routes/gifts.py", "create_gift_for_user"),
    ("api/v2/routes/keys/user/addons.py", "user_key_apply_addons"),
    ("services/keys.py", "calculate_renewal_pricing"),
    ("handlers/tariffs/addons/pack_mode/router.py", "handle_addons_confirm"),
    ("handlers/tariffs/addons/config_mode/router.py", "handle_addons_confirm"),
    ("handlers/payments/fast_payment_flow.py", "resume_checkout"),
    ("handlers/payments/checkout_coupon.py", "apply_checkout_coupon"),
)


def _balance_reads(path, func_name):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == func_name)
    reads = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "balance" for t in node.targets):
            for call in ast.walk(node.value):
                if isinstance(call, ast.Call) and ast.unparse(call.func) == "get_balance":
                    reads.append(any(k.arg == "exact" and getattr(k.value, "value", None) is True
                                     for k in call.keywords))
    return reads


class BalanceExactRest(unittest.TestCase):
    def test_decisions_use_exact_balance(self):
        for path, fn in DECISIONS:
            with self.subTest(path=path, fn=fn):
                reads = _balance_reads(path, fn)
                self.assertTrue(reads)
                self.assertTrue(all(reads))

    def test_trial_shortfall_rounds_up(self):
        # С точным балансом 99.97 при цене 100 int() дал бы доплату 0 ₽.
        src = (ROOT / "handlers/keys/create/flow.py").read_text(encoding="utf-8")
        self.assertIn("shortfall = ceil(trial_price - balance)", src)


if __name__ == "__main__":
    unittest.main()
