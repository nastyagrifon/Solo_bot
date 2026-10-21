"""Провал продления в панели обязан быть слышен.

21.10.2026 одиннадцать оплативших клиентов остались без доступа: юзер в панели
пропал, бот держал ключ живым, а единственный след неудачи писался уровнем
``debug`` при ``LOGGING_LEVEL=INFO`` — то есть никуда. Узнали из тикетов
поддержки, а не из журнала.

Проверяем ветку неудачи ``renew_on_remnawave``: уровень ``error`` и опознаваемый
клиент в сообщении, иначе инцидент снова будет невидим.
Запуск из корня проекта: ``venv/bin/python -m unittest discover -s tests -v``
"""

import asyncio
import importlib.util
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

ЗАПИСИ: list[tuple[str, str]] = []


def _stub(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


def _писатель(уровень):
    def пишет(сообщение, *a, **k):
        ЗАПИСИ.append((уровень, str(сообщение)))
    return пишет


# renewal.py берёт журнал как ``CLOGGER as logger`` — подменять надо CLOGGER.
_ЖУРНАЛ = types.SimpleNamespace(
    **{lvl: _писатель(lvl) for lvl in
       ("debug", "info", "warning", "error", "exception", "critical")})
_stub("logger", CLOGGER=_ЖУРНАЛ, logger=_ЖУРНАЛ,
      PANEL_REMNA="[Remnawave]", PANEL_XUI="[3x-ui]")
_stub("sqlalchemy")
_stub("sqlalchemy.ext")
_stub("sqlalchemy.ext.asyncio", AsyncSession=object)
_база = _stub("database")
# Имён из database ядро тянет много и список растёт; отдаём пустышку на любое,
# чтобы тест не становился skip при каждом новом импорте в renewal.py.
_база.__getattr__ = lambda имя: (lambda *a, **k: None)
_stub("hooks")
_stub("hooks.processors", process_get_cryptolink_after_renewal=lambda *a, **k: None)
_stub("panels")
_stub("panels._3xui", extend_client_key=lambda *a, **k: None,
      get_xui_instance=lambda *a, **k: None)
_stub("panels.remnawave_runtime",
      invalidate_remnawave_profile=lambda *a, **k: None,
      with_remnawave_api=lambda *a, **k: None)
_stub("services")
_stub("services.clusters", ALLOWED_GROUP_CODES=set())
_stub("settings")
_stub("settings.config", SUPERNODE=False)

_ПАКЕТ = types.ModuleType("services.operations")
_ПАКЕТ.__path__ = [str(ROOT / "services" / "operations")]
sys.modules["services.operations"] = _ПАКЕТ
_stub("services.operations.aggregated_links", make_aggregated_link=lambda *a, **k: None)
_stub("services.operations.utils", resolve_cluster=lambda *a, **k: None)

_SPEC = importlib.util.spec_from_file_location(
    "services.operations.renewal", ROOT / "services" / "operations" / "renewal.py"
)
_mod = importlib.util.module_from_spec(_SPEC)
try:
    _SPEC.loader.exec_module(_mod)
    LOADED, LOAD_ERROR = True, None
except Exception as exc:                                             # noqa: BLE001
    LOADED, LOAD_ERROR = False, exc


КЛАСТЕР = [{"panel_type": "remnawave", "inbound_id": "sq-1", "server_name": "vip"}]
ВЫЗОВ = dict(
    cluster=КЛАСТЕР, email="testkey1", client_id="fixture-uuid-aaaa-bbbb-cccccccccccc",
    tg_id=1234567, new_expiry_time=1794903738815, total_gb=0,
    hwid_device_limit=1, session=None, reset_traffic=False,
)


@unittest.skipUnless(LOADED, f"renewal.py не загрузился: {LOAD_ERROR}")
class ПровалСлышен(unittest.TestCase):
    def setUp(self):
        ЗАПИСИ.clear()

    def _прогон(self, итог):
        async def панель(*a, **k):
            return итог
        _mod.with_remnawave_api = панель
        return asyncio.run(_mod.renew_on_remnawave(**ВЫЗОВ))

    def test_успех_остаётся_info(self):
        self.assertTrue(self._прогон({"ok": True}))
        уровни = {у for у, _ in ЗАПИСИ}
        self.assertIn("info", уровни)
        self.assertNotIn("error", уровни)

    def test_провал_пишется_уровнем_error(self):
        self.assertFalse(self._прогон(None))
        ошибки = [т for у, т in ЗАПИСИ if у == "error"]
        self.assertEqual(len(ошибки), 1, f"ждём одну error-строку, получили {ЗАПИСИ}")

    def test_в_ошибке_есть_чем_найти_клиента(self):
        self._прогон(None)
        текст = "".join(т for у, т in ЗАПИСИ if у == "error")
        for опознание in ("testkey1", "1234567", "fixture-uuid-aaaa-bbbb-cccccccccccc"):
            self.assertIn(опознание, текст, f"в error-строке нет {опознание}: {текст}")

    def test_провал_не_пишется_в_debug(self):
        self._прогон(None)
        отладка = [т for у, т in ЗАПИСИ if у == "debug" and "продлить" in т]
        self.assertEqual(отладка, [], "провал всё ещё уходит в debug и будет невидим")


if __name__ == "__main__":
    unittest.main(verbosity=2)
