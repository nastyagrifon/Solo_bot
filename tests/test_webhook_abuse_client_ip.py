"""Бан вебхуков считается по адресу собеседника, а не по заголовку от клиента.

Раньше `get_webhook_client_ip` брал первый элемент X-Forwarded-For без
проверки, кто его прислал: десяток запросов с плохой подписью и чужим адресом
в заголовке банили адрес платёжки, а смена заголовка обходила бан.
"""

import importlib.util
import sys
import types
import unittest

from pathlib import Path

from aiohttp.test_utils import make_mocked_request

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _stub(name: str, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod
    return mod


async def _noop(*a, **k):
    return None


_stub("core")
_stub("core.redis_cache", cache_delete=_noop, cache_get=_noop, cache_incr=_noop, cache_key=lambda *a: ":".join(map(str, a)), cache_set=_noop)
_stub("logger", logger=types.SimpleNamespace(warning=lambda *a, **k: None))
_stub("settings")
_stub("settings.cache_config", WEBHOOK_ABUSE_BLOCK_TTL_SEC=300, WEBHOOK_ABUSE_FAIL_THRESHOLD=10, WEBHOOK_ABUSE_FAIL_WINDOW_SEC=60)
_stub("utils")
if (ROOT / "utils/client_ip.py").exists():
    _load("utils.client_ip", "utils/client_ip.py")
webhook_abuse = _load("core.webhook_abuse", "core/webhook_abuse.py")


def _request(peer: str, headers: dict[str, str]):
    transport = types.SimpleNamespace(get_extra_info=lambda key, default=None: (peer, 443) if key == "peername" else default)
    return make_mocked_request("POST", "/freekassa/webhook", headers=headers, transport=transport)


class WebhookClientIp(unittest.TestCase):
    def test_pryamoy_zapros_ne_verit_xff(self):
        # Злоумышленник ходит напрямую и подставляет адрес платёжки.
        req = _request("198.51.100.66", {"X-Forwarded-For": "203.0.113.10"})
        self.assertEqual(webhook_abuse.get_webhook_client_ip(req), "198.51.100.66")

    def test_pryamoy_zapros_ne_verit_real_ip(self):
        req = _request("198.51.100.66", {"X-Real-IP": "203.0.113.10"})
        self.assertEqual(webhook_abuse.get_webhook_client_ip(req), "198.51.100.66")

    def test_smena_zagolovka_ne_menyaet_klyuch_bana(self):
        ips = {webhook_abuse.get_webhook_client_ip(_request("198.51.100.66", {"X-Forwarded-For": f"10.9.8.{i}"})) for i in range(5)}
        self.assertEqual(ips, {"198.51.100.66"})

    def test_za_doverennym_proksi_beryotsya_klient(self):
        # Caddy на петле дописывает реальный адрес справа; подделка клиента левее.
        req = _request("127.0.0.1", {"X-Forwarded-For": "203.0.113.10, 198.51.100.66"})
        self.assertEqual(webhook_abuse.get_webhook_client_ip(req), "198.51.100.66")

    def test_bez_zagolovkov_peer(self):
        self.assertEqual(webhook_abuse.get_webhook_client_ip(_request("198.51.100.66", {})), "198.51.100.66")


if __name__ == "__main__":
    unittest.main()
