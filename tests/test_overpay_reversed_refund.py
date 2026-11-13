"""Overpay: ``reversed`` после зачисленного ``approved`` откатывает баланс.

``approved`` — авторизация карты, и по ней баланс зачисляется. Если затем
приходит ``reversed`` (авторизацию сняли, денег нет), он попадал в группу
неудачных и шёл в отмену со статусом ``failed``; для уже успешного платежа
это «already_processed» — клиент оставался с деньгами, которых нет.

Проверяем: вебхук с ``reversed`` идёт в ``process_cancelled_payment`` со
статусом возврата, который конвейер обрабатывает списанием зачисленного.
Модуль ``services/payments/overpay/webhook.py`` грузится отдельно с
заглушками, пакет ``database`` не импортируется.
Запуск из корня проекта: ``venv/bin/python -m unittest tests.test_overpay_reversed_refund -v``
"""

import ast
import asyncio
import base64
import importlib.util
import json
import pathlib
import re
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_SAVED: dict = {}
MOD = None
CALLS: list = []


def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


class _Response:
    def __init__(self, status=200, text=""):
        self.status = status
        self.text = text


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


async def _get_payment(session, payment_id):
    return {"amount": 100.0, "user_id": 7, "status": "success"}


async def _cancelled(provider, parsed, *, new_status="cancelled"):
    CALLS.append(("cancel", new_status))
    return types.SimpleNamespace(ok=True, already_processed=False)


async def _success(provider, parsed, **k):
    CALLS.append(("success", None))
    return types.SimpleNamespace(ok=True, already_processed=False)


async def _false(*a, **k):
    return False


def setUpModule():
    global MOD

    class UserId(int):
        pass

    pkg = lambda n: _mod(n, __path__=[])  # noqa: E731
    log = types.SimpleNamespace(**{lvl: (lambda *a, **k: None) for lvl in ("debug", "info", "warning", "error")})
    stubs = {
        "aiohttp": _mod("aiohttp", __path__=[], web=_mod("aiohttp.web", Response=_Response, Request=object)),
        "core": pkg("core"),
        "core.webhook_abuse": _mod("core.webhook_abuse", get_webhook_client_ip=lambda r: "1.2.3.4",
                                   is_webhook_ip_blocked=_false),
        "database": _mod("database", __path__=[], async_session_maker=_Session,
                         get_payment_by_payment_id=_get_payment),
        "database.access": pkg("database.access"),
        "database.access.resolution": _mod("database.access.resolution", UserId=UserId),
        "handlers": pkg("handlers"),
        "handlers.payments": pkg("handlers.payments"),
        "handlers.payments.overpay": pkg("handlers.payments.overpay"),
        "handlers.payments.overpay.service": _mod("handlers.payments.overpay.service", get_overpay_order=None),
        "logger": _mod("logger", logger=log),
        "services": pkg("services"),
        "services.payments": pkg("services.payments"),
        "services.payments.owner_refs": _mod("services.payments.owner_refs", parse_payment_owner=int),
        "services.payments.pipeline": _mod("services.payments.pipeline", ParsedPayment=types.SimpleNamespace,
                                           process_cancelled_payment=_cancelled,
                                           process_success_payment=_success),
        "settings": pkg("settings"),
        "settings.config": _mod("settings.config", OVERPAY_WEBHOOK_USERNAME="u", OVERPAY_WEBHOOK_PASSWORD="p"),
    }
    stubs["aiohttp.web"] = stubs["aiohttp"].web
    name = "services.payments.overpay.webhook"
    for n in [*stubs, name]:
        _SAVED[n] = sys.modules.get(n)
    sys.modules.update(stubs)
    spec = importlib.util.spec_from_file_location(name, ROOT / "services" / "payments" / "overpay" / "webhook.py")
    MOD = importlib.util.module_from_spec(spec)
    sys.modules[name] = MOD
    spec.loader.exec_module(MOD)


def tearDownModule():
    for n, m in _SAVED.items():
        if m is None:
            sys.modules.pop(n, None)
        else:
            sys.modules[n] = m


class _Request:
    def __init__(self, status):
        self.headers = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
        self._body = json.dumps({"id": "ord1", "status": status, "merchantTransactionId": "a_b_7"}).encode()

    async def read(self):
        return self._body


def _send(status):
    CALLS.clear()
    resp = asyncio.run(MOD.overpay_webhook(_Request(status)))
    return resp.status, list(CALLS)


class OverpayReversedRefund(unittest.TestCase):
    def test_reversed_goes_to_refund(self):
        reversal = re.search(r"_REVERSAL_STATUSES\s*=\s*(\{[^}]*\})",
                             (ROOT / "services" / "payments" / "pipeline.py").read_text(encoding="utf-8"))
        status, calls = _send("reversed")
        self.assertEqual(status, 200)
        self.assertEqual(len(calls), 1)
        kind, new_status = calls[0]
        self.assertEqual(kind, "cancel")
        self.assertIn(new_status, ast.literal_eval(reversal.group(1)))  # конвейер откатывает зачисленное только для них

    def test_declined_stays_failed(self):
        self.assertEqual(_send("declined")[1], [("cancel", "failed")])

    def test_approved_still_credits(self):
        self.assertEqual(_send("approved")[1], [("success", None)])


if __name__ == "__main__":
    unittest.main()
