"""Журнал доступа к API пишет адрес клиента, а не контейнера кабинета.

Кабинет ходит в API со своего сервера, поэтому peer для бота - его контейнер.
Журнал брал `request.client.host`, и каждая строка несла один и тот же адрес
докер-сети: сканер был неотличим от клиента.
"""

import ast
import unittest

from pathlib import Path
from types import SimpleNamespace

from utils.client_ip import client_ip


def _request(peer: str, headers: dict[str, str]):
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers={k.lower(): v for k, v in headers.items()})


class ApiLogClientIp(unittest.TestCase):
    def test_zapros_kabineta_dayot_adres_klienta(self):
        # Так запрос выглядит на деле: прокси ставит в цепочку адрес контейнера, клиент едет в X-Real-IP.
        req = _request("127.0.0.1", {"X-Forwarded-For": "172.19.0.2", "X-Real-IP": "203.0.113.7"})
        self.assertEqual(client_ip(req), "203.0.113.7")

    def test_bez_zagolovkov_ostayotsya_peer(self):
        self.assertEqual(client_ip(_request("172.19.0.2", {})), "172.19.0.2")

    def test_zhurnal_ne_chitaet_peer_napryamuyu(self):
        tree = ast.parse(Path("audit/__init__.py").read_text(encoding="utf-8"))
        func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "log_api_access")
        attrs = {n.attr for n in ast.walk(func) if isinstance(n, ast.Attribute)}
        names = {n.id for n in ast.walk(func) if isinstance(n, ast.Name)}
        self.assertNotIn("host", attrs)
        self.assertIn("resolve_client_ip", names)


if __name__ == "__main__":
    unittest.main()
