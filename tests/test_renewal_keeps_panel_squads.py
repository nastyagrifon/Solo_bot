"""Продление и миграция не сбрасывают сквады, выставленные в панели.

Тот же тариф и подгруппа — в панель уходят её текущие сквады; смена тарифа —
тарифные; пустой внешний сквад тарифа значит «не задан» и не затирает
текущий. Ядро подменено заглушками, панель — фейком.
Запуск из корня проекта: ``venv/bin/python -m unittest discover -s tests -v``
"""

import asyncio
import contextlib
import importlib.util
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SAVED = {}


def _stub(name, **attrs):
    SAVED.setdefault(name, sys.modules.get(name))
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


async def _none(*a, **k):
    return None


def _restore():
    for name, original in SAVED.items():
        if original is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = original
    SAVED.clear()


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_all():
    log = types.SimpleNamespace(**{lvl: (lambda *a, **k: None) for lvl in ("debug", "info", "warning", "error")})
    _stub("logger", CLOGGER=log, logger=log, PANEL_REMNA="[R]", PANEL_XUI="[X]")
    _stub("sqlalchemy")
    _stub("sqlalchemy.ext")
    _stub("sqlalchemy.ext.asyncio", AsyncSession=object)
    db = _stub("database")
    db.__path__ = []
    db.__getattr__ = lambda name: _none
    _stub("database.access")
    _stub("database.access.resolution", UserId=int, panel_identity_fields=_none, subscription_owner_ref=_none)
    _stub("database.keys", resolve_key_operation_owner=_none)
    _stub("database.traffic_notifications", reset_traffic_notification_state=_none)
    _stub("services")
    _stub("services.subscription_links", saved_public_subscription_tail=lambda *a, **k: None)
    _stub("services.clusters", ALLOWED_GROUP_CODES=set())
    _stub("utils")
    _stub("utils.traffic_resources", traffic_resource_id=lambda *a, **k: None)
    _stub("hooks")
    _stub("hooks.processors", process_get_cryptolink_after_renewal=_none)
    _stub("panels")
    _stub("panels._3xui", extend_client_key=_none, get_xui_instance=_none, ClientConfig=object, add_client=_none)
    _stub("panels.remnawave_runtime", invalidate_remnawave_profile=_none, with_remnawave_api=_none)
    _stub("settings")
    _stub("settings.config", SUPERNODE=False, PUBLIC_LINK="", HAPP_CRYPTOLINK=False,
          REMNAWAVE_LOGIN="", REMNAWAVE_PASSWORD="")
    _stub("core")
    _stub("core.bootstrap", MODES_CONFIG={})
    pkg = _stub("services.operations")
    pkg.__path__ = [str(ROOT / "services/operations")]
    _stub("services.operations.aggregated_links", make_aggregated_link=_none)
    _stub("services.operations.utils", resolve_cluster=_none, bytes_from_gb=lambda gb: int(gb or 0) * 2**30)
    try:
        renewal = _load("services/operations/renewal.py", "services.operations.renewal")
        migration = _load("services/tariffs/subgroup_migration.py", "subgroup_migration_under_test")
    finally:
        sys.modules.pop("services.operations.renewal", None)
        sys.modules.pop("subgroup_migration_under_test", None)
        _restore()
    return renewal, migration


renewal, migration = _load_all()


class FakePanel:
    def __init__(self, user):
        self.user, self.updates, self.reads = user, [], 0

    async def get_user_by_uuid(self, uuid, username=None):
        self.reads += 1
        return self.user

    async def update_user(self, **kwargs):
        self.updates.append(kwargs)
        return True

    async def get_subscription_by_username(self, email):
        return None


PANEL_USER = {
    "externalSquadUuid": "ext-panel",
    "activeInternalSquads": [{"uuid": "int-panel"}],
}
CLUSTER = [{"panel_type": "remnawave", "inbound_id": "int-cluster", "server_name": "n1"}]


def run(coro):
    return asyncio.run(coro)


class RenewOnRemnawaveTest(unittest.TestCase):
    def _renew(self, external, keep, user=PANEL_USER):
        panel = FakePanel(dict(user) if user else None)

        async def api(session, ref, op, **k):
            return await op(panel)

        renewal.with_remnawave_api = api
        ok = run(renewal.renew_on_remnawave(
            cluster=CLUSTER, client_id="cid", email="mail", tg_id=1, new_expiry_time=1_800_000_000_000,
            total_gb=0, hwid_device_limit=1, session=None, reset_traffic=False,
            external_squad_uuid=external, keep_squads=keep,
        ))
        self.assertTrue(ok)
        return panel, panel.updates[-1]

    def test_same_tariff_sends_current_panel_squads(self):
        _, sent = self._renew("ext-tariff", keep=True)
        self.assertEqual(sent["external_squad_uuid"], "ext-panel")
        self.assertEqual(sent["active_user_inbounds"], ["int-panel"])

    def test_changed_tariff_sends_tariff_squads(self):
        panel, sent = self._renew("ext-tariff", keep=False)
        self.assertEqual(sent["external_squad_uuid"], "ext-tariff")
        self.assertEqual(sent["active_user_inbounds"], ["int-cluster"])
        self.assertEqual(panel.reads, 0)

    def test_empty_tariff_squad_keeps_current(self):
        _, sent = self._renew("", keep=False)
        self.assertEqual(sent["external_squad_uuid"], "ext-panel")
        self.assertEqual(sent["active_user_inbounds"], ["int-cluster"])

    def test_unread_panel_user_does_not_wipe_squad(self):
        _, sent = self._renew("", keep=True, user=None)
        self.assertIsNone(sent["external_squad_uuid"])
        self.assertEqual(sent["active_user_inbounds"], ["int-cluster"])

    def test_renewal_does_not_send_tag(self):
        # Тег TESTER ставит настройка тестировщиков; продление его не передаёт, а значит и не сбрасывает.
        for external, keep in (("ext-tariff", True), ("ext-tariff", False), ("", True)):
            with self.subTest(external=external, keep=keep):
                self.assertNotIn("tag", self._renew(external, keep=keep)[1])

    def test_empty_panel_internal_squads_fall_back_to_cluster(self):
        _, sent = self._renew("x", keep=True, user={"externalSquadUuid": None, "activeInternalSquads": []})
        self.assertEqual(sent["active_user_inbounds"], ["int-cluster"])


class RenewKeyRuleTest(unittest.TestCase):
    """Признак «тариф и подгруппа не менялись» считает renew_key_in_cluster."""

    def _keep(self, plan, key_tariff, target=None, old=None):
        seen = {}

        async def renew_on_remnawave(**kwargs):
            seen.update(kwargs)
            return False

        async def get_servers(session):
            return {"c1": CLUSTER}

        async def get_key_details(session, email):
            return {"client_id": "cid", "user_id": 1, "server_id": "c1", "tariff_id": key_tariff, "key": None}

        async def tariff(session, tid):
            return {"external_squad": "", "configurable": True}

        async def owner_ref(session, ref):
            return 1

        async def filter_by_tariff(session, cluster, plan, cid):
            return cluster

        async def no_3xui(**k):
            return [], []

        patch = dict(renew_on_remnawave=renew_on_remnawave, get_servers=get_servers,
                     get_key_details=get_key_details, get_tariff_by_id=tariff,
                     subscription_owner_ref=owner_ref, filter_cluster_by_tariff=filter_by_tariff,
                     filter_cluster_by_subgroup=filter_by_tariff, renew_on_3xui=no_3xui,
                     resolve_device_limit_from_group=_none)
        old_attrs = {k: getattr(renewal, k) for k in patch}
        renewal.__dict__.update(patch)
        try:
            run(renewal.renew_key_in_cluster("c1", "mail", "cid", 1, 0, None, plan=plan,
                                             target_subgroup=target, old_subgroup=old))
        finally:
            renewal.__dict__.update(old_attrs)
        return seen["keep_squads"]

    def test_same_plan_keeps(self):
        self.assertTrue(self._keep(3, 3))

    def test_other_plan_does_not_keep(self):
        self.assertFalse(self._keep(4, 3))


class MigrationExternalSquadTest(unittest.TestCase):
    def _update(self, external):
        panel = FakePanel(dict(PANEL_USER))

        async def login(*a):
            return True

        panel.login = login

        @contextlib.asynccontextmanager
        async def remnawave_api(url):
            yield panel

        runtime = types.ModuleType("panels.remnawave_runtime")
        runtime.remnawave_api = remnawave_api
        utils = types.ModuleType("services.operations.utils")
        utils.bytes_from_gb = lambda gb: 0
        saved = {k: sys.modules.get(k) for k in ("panels", "panels.remnawave_runtime", "services",
                                                  "services.operations", "services.operations.utils")}
        sys.modules.update({"panels": types.ModuleType("panels"), "panels.remnawave_runtime": runtime,
                            "services": types.ModuleType("services"),
                            "services.operations": types.ModuleType("services.operations"),
                            "services.operations.utils": utils})
        try:
            run(migration.ensure_on_remnawave(
                servers=[{"api_url": "u", "inbound_id": "int-cluster"}], email="mail", client_id="cid", tg_id=1,
                new_expiry_time=1_800_000_000_000, total_gb=0, hwid_device_limit=1, reset_traffic=False,
                attempt_update_first=True, external_squad_uuid=external,
            ))
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
        return panel.updates[0]["external_squad_uuid"]

    def test_empty_tariff_squad_keeps_current(self):
        self.assertEqual(self._update(""), "ext-panel")

    def test_tariff_squad_wins(self):
        self.assertEqual(self._update("ext-tariff"), "ext-tariff")


if __name__ == "__main__":
    unittest.main()
