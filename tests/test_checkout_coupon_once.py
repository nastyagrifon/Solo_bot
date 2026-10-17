"""Купон в чекауте применяется один раз.

Автоприменённый купон уже снизил цену в корзине. Если потом принять введённый
вручную код, скидка считается от уже сниженной суммы, и две скидки
складываются. Проверяем, что повторное применение отклоняется.

Зависимости модуля подменяются заглушками в sys.modules, поэтому тест идёт
без базы и без aiogram.
Запуск из корня проекта: ``venv/bin/python -m unittest discover -s tests -v``
"""

import asyncio
import importlib.util
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_resolve_calls = []


def _stub_modules():
    """Заглушки ровно для того, что импортирует checkout_coupon."""
    async def resolve_percent_coupon(session, **kwargs):
        _resolve_calls.append(kwargs)
        # «успешная» скидка: цена вдвое ниже
        base = float(kwargs.get("base_price_rub") or 0)
        return base / 2, base / 2, 777, kwargs.get("coupon_code") or "CODE"

    async def get_balance(session, user_ref):
        return 0.0

    async def noop(*a, **k):
        return None

    def mod(name, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[name] = m
        return m

    mod("database")
    mod("database.coupons", get_coupon_by_code_ci=noop)
    mod("database.temporary_data", create_temporary_data=noop)
    mod("database.users", get_balance=get_balance)
    mod("services")
    mod("services.coupons", resolve_percent_coupon=resolve_percent_coupon)
    mod("settings")
    mod("settings.texts", FASTFLOW_COUPON_APPLIED_TEMPLATE="скидка {discount}")
    mod("logger", logger=types.SimpleNamespace(
        **{lvl: (lambda *a, **k: None) for lvl in
           ("debug", "info", "warning", "error", "exception", "critical")}))


_stub_modules()
_SPEC = importlib.util.spec_from_file_location(
    "checkout_coupon", ROOT / "handlers" / "payments" / "checkout_coupon.py"
)
_mod = importlib.util.module_from_spec(_SPEC)
try:
    _SPEC.loader.exec_module(_mod)
    LOADED = True
except Exception as exc:                                             # noqa: BLE001
    LOADED = False
    LOAD_ERROR = exc


@unittest.skipUnless(LOADED, "модуль не загрузился без полного окружения")
class CouponAppliedOnce(unittest.TestCase):
    def setUp(self):
        _resolve_calls.clear()

    def apply(self, payload, code=None):
        return asyncio.run(
            _mod.apply_checkout_coupon(
                None, 1, "temp-key", payload, 1000, coupon_code=code
            )
        )

    def test_second_code_rejected_when_code_already_applied(self):
        """Купон уже применён: второй код не должен давать скидку снова."""
        payload = {"applied_coupon_code": "FIRST", "coupon_id": 1, "cost": 500}
        out, amount, note = self.apply(payload, code="SECOND")
        self.assertEqual(out, payload)
        self.assertEqual(amount, 1000)
        self.assertEqual(_resolve_calls, [], "скидку считать не должны")

    def test_rejected_when_only_coupon_id_present(self):
        """Код мог не сохраниться, но id купона говорит, что скидка уже есть."""
        payload = {"coupon_id": 42, "cost": 500}
        out, amount, _ = self.apply(payload, code="SECOND")
        self.assertEqual(out, payload)
        self.assertEqual(amount, 1000)
        self.assertEqual(_resolve_calls, [])

    def test_first_coupon_still_applies(self):
        """Чистая корзина: купон обязан примениться, иначе фикс сломал бы скидки."""
        payload = {"cost": 500}
        out, _, _ = self.apply(payload, code="FIRST")
        self.assertEqual(len(_resolve_calls), 1)
        self.assertEqual(out.get("applied_coupon_code"), "FIRST")


if __name__ == "__main__":
    unittest.main()
