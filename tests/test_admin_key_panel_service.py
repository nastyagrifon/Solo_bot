"""Поля юзера панели в карточке ключа: действия с панелью и размер кнопок.

Панель подменена фейком: проверяем, что уходит ровно одно поле, что правку
сверяем по ответу панели и что сбой не выдаётся за успех.
Запуск из корня проекта: ``venv/bin/python -m unittest discover -s tests -v``
"""

import asyncio
import importlib.util
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / "handlers/admin/users/panel_user"


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class FakeApi:
    """Панель в памяти. echo=False — панель отвечает 200, но поле не меняет."""

    def __init__(self, user, echo=True, status=200, devices_after_clear=None):
        self.user = dict(user)
        self.echo, self.status = echo, status
        self.devices = [{"hwid": "x"}]
        self.devices_after_clear = [] if devices_after_clear is None else devices_after_clear
        self.patches = []

    async def get_user_by_uuid(self, uuid, username=None):
        return dict(self.user)

    async def _request(self, method, path, json=None, params=None):
        if self.status != 200:
            return FakeResponse(self.status)
        if method == "GET" and path.endswith("-squads"):
            kind = path.strip("/").split("-")[0]
            return FakeResponse(200, {"response": {f"{kind}Squads": [{"uuid": "a", "name": "A"}]}})
        self.patches.append((method, path, json))
        if self.echo:
            self.user.update({k: v for k, v in json.items() if k != "uuid"})
        return FakeResponse(200, {"response": dict(self.user)})

    async def clear_all_hwid_devices(self, uuid, username=None):
        self.devices = list(self.devices_after_clear)
        return True

    async def get_user_hwid_devices(self, uuid, username=None):
        return self.devices


def _load_service(api, calls):
    runtime = types.ModuleType("panels.remnawave_runtime")

    async def with_remnawave_api(session, server_ref, operation, **kwargs):
        calls.append(("api", server_ref, kwargs.get("client_id")))
        return None if api is None else await operation(api)

    async def invalidate_remnawave_profile(session, server_ref, client_id):
        calls.append(("invalidate", server_ref, client_id))

    runtime.with_remnawave_api = with_remnawave_api
    runtime.invalidate_remnawave_profile = invalidate_remnawave_profile
    sys.modules.setdefault("panels", types.ModuleType("panels"))
    sys.modules["panels.remnawave_runtime"] = runtime
    spec = importlib.util.spec_from_file_location("panel_user_service", PKG / "service.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Key:
    server_id, client_id, email, remnawave_link, key, tg_id = "srv-1", "cid-1", "mail@x", "https://s/1", None, 42


def run(coro):
    return asyncio.run(coro)


class PatchOneFieldTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.api = FakeApi({"uuid": "u-1", "tag": None, "activeInternalSquads": [{"uuid": "a"}]})
        self.svc = _load_service(self.api, self.calls)

    def test_sends_only_uuid_and_field_to_key_server(self):
        run(self.svc.set_field(None, Key, "tag", "VIP"))
        self.assertEqual(self.api.patches, [("PATCH", "/users", {"uuid": "u-1", "tag": "VIP"})])
        self.assertIn(("api", "srv-1", "cid-1"), self.calls)
        self.assertIn(("invalidate", "srv-1", "cid-1"), self.calls)

    def test_new_panel_without_uuid_is_addressed_by_id(self):
        self.api.user = {"id": 10, "uuid": "u-1", "tag": None}
        run(self.svc.set_field(None, Key, "tag", "VIP"))
        self.assertEqual(self.api.patches[-1], ("PATCH", "/users", {"id": 10, "tag": "VIP"}))

    def test_internal_squads_order_does_not_matter(self):
        self.api.user["activeInternalSquads"] = [{"uuid": "b"}, {"uuid": "a"}]
        saved = run(self.svc.set_field(None, Key, self.svc.INTERNAL, ["a", "b"]))
        self.assertEqual(sorted(self.svc.internal_squad_uuids(saved)), ["a", "b"])

    def test_panel_ignored_change_is_error_not_success(self):
        self.api.echo = False
        with self.assertRaises(self.svc.PanelUserError):
            run(self.svc.set_field(None, Key, "description", "note"))
        self.assertNotIn(("invalidate", "srv-1", "cid-1"), self.calls)

    def test_panel_rejected_is_error(self):
        self.api.status = 400
        with self.assertRaises(self.svc.PanelUserError):
            run(self.svc.set_field(None, Key, "telegramId", 7))

    def test_panel_unreachable_is_error(self):
        svc = _load_service(None, self.calls)
        with self.assertRaises(svc.PanelUserError):
            run(svc.set_field(None, Key, "externalSquadUuid", "ext"))


class LoadTest(unittest.TestCase):
    def test_user_and_squads_in_one_panel_trip(self):
        calls = []
        api = FakeApi({"uuid": "u-1"})
        svc = _load_service(api, calls)
        user, squads = run(svc.load(None, Key, "internal"))
        # Имя сквада обязательно: без него админ видит в списке голые uuid.
        self.assertEqual((user["uuid"], squads[0]["name"]), ("u-1", "A"))
        self.assertEqual(len([c for c in calls if c[0] == "api"]), 1)

    def test_missing_squad_list_is_error(self):
        api = FakeApi({"uuid": "u-1"})
        api.status = 500
        svc = _load_service(api, [])
        with self.assertRaises(svc.PanelUserError):
            run(svc.load(None, Key, "external"))


class ClearDevicesTest(unittest.TestCase):
    def test_success_only_when_list_is_empty(self):
        calls = []
        svc = _load_service(FakeApi({"uuid": "u-1"}), calls)
        run(svc.clear_devices(None, Key))

    def test_devices_left_is_error(self):
        svc = _load_service(FakeApi({"uuid": "u-1"}, devices_after_clear=[{"hwid": "y"}]), [])
        with self.assertRaises(svc.PanelUserError):
            run(svc.clear_devices(None, Key))


class SquadListTest(unittest.TestCase):
    def setUp(self):
        self.svc = _load_service(FakeApi({}), [])

    def test_squads_as_objects_or_bare_uuids(self):
        self.assertEqual(self.svc.internal_squad_uuids({"activeInternalSquads": [{"uuid": "a"}, "b"]}), ["a", "b"])

    def test_toggle(self):
        self.assertEqual(self.svc.toggle(["a"], "b"), ["a", "b"])
        self.assertEqual(self.svc.toggle(["a", "b"], "a"), ["b"])


try:
    import aiogram  # noqa: F401
except ImportError:  # без aiogram экраны не собрать
    aiogram = None


def _load_views():
    """views.py с подменёнными заголовками: section отдаёт строки как есть, чтобы проверять текст."""
    from aiogram.filters.callback_data import CallbackData

    def section(title, *lines):
        return "\n".join((title, *lines))

    stubs = {
        "settings": types.ModuleType("settings"),
        "settings.buttons": types.SimpleNamespace(BACK="Назад"),
        "handlers": types.ModuleType("handlers"),
        "handlers.admin": types.ModuleType("handlers.admin"),
        "handlers.admin.panel": types.ModuleType("handlers.admin.panel"),
        "handlers.admin.panel.headers": types.SimpleNamespace(
            card=lambda *b: "\n".join(b), menu_text=lambda *p, **k: "\n".join(p), section=section
        ),
        "handlers.admin.users": types.ModuleType("handlers.admin.users"),
        "handlers.admin.users.panel_user": types.ModuleType("handlers.admin.users.panel_user"),
        "handlers.admin.users.keyboard": types.SimpleNamespace(LegacyTgRefAlias=type("LegacyTgRefAlias", (), {})),
    }
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("handlers.admin.users.panel_user.views", PKG / "views.py")
        views = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(views)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return views, CallbackData


@unittest.skipIf(aiogram is None, "aiogram не установлен")
class CallbackSizeTest(unittest.TestCase):
    """Telegram режет callback_data длиннее 64 байт: кнопка молча не нажимается."""

    def test_worst_case_fits_64_bytes(self):
        views, _ = _load_views()
        user_id = 2**63 - 1  # самый длинный id; +1 байт на букву типа id в ядре
        key_ref = "f" * 12  # build_key_ref: blake2s по 6 байт → 12 hex
        longest = max(
            views.PanelUserCallback(action=a, user_id=user_id, key_ref=key_ref, field="note", idx=99).pack()
            for a in ("back", "ext_set", "int_tg", "int_save", "clr", "mv_ok")
        )
        self.assertLessEqual(len(longest.encode()) + 1, 64, longest)


@unittest.skipIf(aiogram is None, "aiogram не установлен")
class PanelSectionTest(unittest.TestCase):
    """Секция «Панель» в карточке ключа."""

    def setUp(self):
        self.views, _ = _load_views()

    def test_names_not_uuids(self):
        user = {
            "externalSquadUuid": "e-1",
            "activeInternalSquads": [{"uuid": "i-1", "name": "Основной"}, "i-2"],
            "telegramId": 7,
            "tag": "VIP",
            "description": "заметка",
        }
        text = self.views.panel_section(user, [{"uuid": "e-1", "name": "Внешний А"}])
        for part in ("Внешний А", "Основной, i-2", "TG ID: 7", "Тег: VIP", "Заметка: заметка"):
            self.assertIn(part, text)
        self.assertNotIn("e-1", text)

    def test_long_note_is_cut(self):
        text = self.views.panel_section({"description": "x" * 1000}, [])
        self.assertIn("x" * self.views.NOTE_PREVIEW + "…", text)
        self.assertNotIn("x" * (self.views.NOTE_PREVIEW + 1), text)

    def test_note_is_escaped(self):
        self.assertIn("&lt;b&gt;", self.views.panel_section({"description": "<b>"}, []))


if __name__ == "__main__":
    unittest.main()
