"""Поля панели в карточке клиента: секция, выбор подписки, возврат и ввод TG ID для переноса.

Ядро подменено заглушками: проверяем, что сбой панели не ломает карточку,
что секция и кнопки относятся к выбранной подписке на Remnawave,
что перенос к незнакомому боту клиенту отклоняется до подтверждения и что
сбой панели после переноса показан админу, а не проглочен.
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

try:
    from aiogram.filters.callback_data import CallbackData
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage
except ImportError:  # без aiogram обработчики не собрать
    CallbackData = None


class Calls:
    remnawave: set = {"srv-1"}
    cards: list = []
    users_by_tg: dict = {}
    transfer_result = True
    transferred: list = []


class Key:
    server_id, client_id, email, remnawave_link, key = "srv-1", "cid-1", "mail@x", "https://s/1", None
    tg_id, user_id = 42, 1


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


def _load_handlers():
    class KeyTransferError(Exception):
        pass

    async def get_user_by_tg_id(session, tg):
        return Calls.users_by_tg.get(tg)

    async def transfer_key(session, key, dst, *, actor_tg_id):
        Calls.transferred.append((key.client_id, dst, actor_tg_id))
        return Calls.transfer_result

    async def resolve_admin_key(session, user_id, key_ref):
        return Key

    async def with_remnawave_api(*a, **k):
        return None

    async def noop(*a, **k):
        return None

    async def resolve_remnawave_server_ref(session, server_id):
        return server_id if server_id in Calls.remnawave else None

    class AdminUserEditorCallback(CallbackData, prefix="admin_user"):
        action: str
        user_id: int
        data: str = ""

    log = types.SimpleNamespace(warning=lambda *a, **k: None, info=lambda *a, **k: None)
    package = _module("handlers.admin.users.panel_user")
    package.__path__ = [str(PKG)]
    stubs = {
        "sqlalchemy": _module("sqlalchemy"),
        "sqlalchemy.ext": _module("sqlalchemy.ext"),
        "sqlalchemy.ext.asyncio": _module("sqlalchemy.ext.asyncio", AsyncSession=object),
        "database": _module("database"),
        "database.access": _module("database.access"),
        "database.access.resolution": _module(
            "database.access.resolution", UserId=int, get_user_by_tg_id=get_user_by_tg_id
        ),
        "filters": _module("filters"),
        "filters.admin": _module("filters.admin", IsAdminFilter=lambda: (lambda *a, **k: True)),
        "logger": _module("logger", logger=log),
        "services": _module("services"),
        "services.operations": _module("services.operations"),
        "services.operations.key_transfer": _module(
            "services.operations.key_transfer", KeyTransferError=KeyTransferError, transfer_key=transfer_key
        ),
        "services.users_utils": _module(
            "services.users_utils", build_admin_key_ref=lambda c, e: f"r{c}", resolve_admin_key=resolve_admin_key
        ),
        "services.subscription_keys": _module(
            "services.subscription_keys", resolve_remnawave_server_ref=resolve_remnawave_server_ref
        ),
        "panels": _module("panels"),
        "panels.remnawave_runtime": _module(
            "panels.remnawave_runtime", with_remnawave_api=with_remnawave_api, invalidate_remnawave_profile=noop
        ),
        "settings": _module("settings"),
        "settings.buttons": _module("settings.buttons", BACK="Назад"),
        "handlers": _module("handlers"),
        "handlers.admin": _module("handlers.admin"),
        "handlers.admin.panel": _module("handlers.admin.panel"),
        "handlers.admin.panel.headers": _module(
            "handlers.admin.panel.headers",
            card=lambda *b: "\n".join(b),
            menu_text=lambda *p, **k: "\n".join(p),
            section=lambda t, *lines: "\n".join((t, *lines)),
        ),
        "handlers.admin.users": _module("handlers.admin.users"),
        "handlers.admin.users.keyboard": _module(
            "handlers.admin.users.keyboard",
            AdminUserEditorCallback=AdminUserEditorCallback,
            LegacyTgRefAlias=type("LegacyTgRefAlias", (), {}),
        ),
        "handlers.admin.users.panel_user": package,
    }
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        name = "handlers.admin.users.panel_user.handlers"
        spec = importlib.util.spec_from_file_location(name, PKG / "handlers.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        for sub in ("handlers", "service", "validate", "views", "states"):
            sys.modules.pop(f"handlers.admin.users.panel_user.{sub}", None)
    return module


class FakeMessage:
    def __init__(self, text):
        self.text, self.sent = text, []

    async def answer(self, text, **kwargs):
        self.sent.append((text, kwargs))


def run(coro):
    return asyncio.run(coro)


def _key(client_id, email, server_id="srv-1"):
    return types.SimpleNamespace(client_id=client_id, email=email, server_id=server_id)


def _buttons(rows):
    return [(b.text, b.callback_data) for row in rows for b in row]


@unittest.skipIf(CallbackData is None, "aiogram не установлен")
class ClientPanelTest(unittest.TestCase):
    def setUp(self):
        self.h = _load_handlers()
        Calls.remnawave = {"srv-1"}
        self.loaded = []

        async def load(session, key, squads=None):
            self.loaded.append((key.email, squads))
            return {"tag": "VIP", "externalSquadUuid": "e"}, [{"uuid": "e", "name": "Внешний"}]

        self.h.service.load = load

    def _panel(self, keys, key_ref=None):
        return run(self.h.client_panel(None, 7, keys, key_ref))

    def test_panel_error_does_not_break_card(self):
        async def load(*a, **k):
            raise self.h.PanelUserError("нет")

        self.h.service.load = load
        text, rows = self._panel([_key("a", "a@x")])
        self.assertIn("Панель не ответила", text)
        self.assertTrue(rows)

    def test_unexpected_error_does_not_break_card(self):
        async def load(*a, **k):
            raise RuntimeError("сеть")

        self.h.service.load = load
        self.assertIn("Панель не ответила", self._panel([_key("a", "a@x")])[0])

    def test_fields_in_one_trip(self):
        text, _ = self._panel([_key("a", "a@x")])
        self.assertIn("Тег: VIP", text)
        self.assertIn("Внешний сквад: Внешний", text)
        self.assertEqual(self.loaded, [("a@x", "external")])

    def test_no_remnawave_no_section_no_buttons(self):
        self.assertEqual(self._panel([_key("a", "a@x", "xui"), _key("b", "b@x", "xui")]), (None, []))
        self.assertEqual(self._panel([]), (None, []))
        self.assertEqual(self.loaded, [])

    def test_default_is_first_remnawave_sub(self):
        text, rows = self._panel([_key("x", "x@x", "xui"), _key("a", "a@x"), _key("b", "b@x")])
        self.assertIn("a@x", text.splitlines()[0])
        self.assertEqual(self.loaded, [("a@x", "external")])
        edits = [cb for label, cb in _buttons(rows) if ":users_kpu_" in cb and ":users_kpu_pick:" not in cb]
        self.assertEqual(len(edits), 5)
        self.assertEqual({cb.split(":")[3] for cb in edits}, {"ra"})

    def test_key_ref_selects_sub(self):
        text, rows = self._panel([_key("a", "a@x"), _key("b", "b@x")], "rb")
        self.assertIn("b@x", text.splitlines()[0])
        self.assertEqual({cb.split(":")[3] for _, cb in _buttons(rows)[3:]}, {"rb"})

    def test_unknown_key_ref_falls_back_to_first(self):
        text, _ = self._panel([_key("a", "a@x"), _key("b", "b@x")], "rzz")
        self.assertIn("a@x", text.splitlines()[0])

    def test_picker_only_for_several_subs(self):
        _, rows = self._panel([_key("a", "a@x")])
        self.assertFalse(any(":users_kpu_pick:" in cb for _, cb in _buttons(rows)))
        _, rows = self._panel([_key("a", "a@x"), _key("b", "b@x"), _key("c", "c@x")], "ra")
        picker = [(label, cb.split(":")[3]) for label, cb in _buttons(rows[:1])]
        # По кругу: назад от первой — последняя.
        self.assertEqual(picker, [("◀", "rc"), ("a@x", "ra"), ("▶", "rb")])


class FakeUsersManage:
    """Карточка клиента подменена: запоминаем, с какой подпиской её позвали."""

    @staticmethod
    async def process_user_search(message, state, session, user_id=None, edit=False, actor_tg_id=None, **kw):
        Calls.cards.append((user_id, kw.get("key_ref"), edit, actor_tg_id))


def _with_card(h, coro):
    name = "handlers.admin.users.users_manage"
    saved = sys.modules.get(name)
    sys.modules[name] = FakeUsersManage
    try:
        return run(coro)
    finally:
        if saved is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = saved


@unittest.skipIf(CallbackData is None, "aiogram не установлен")
class ClientCardReturnTest(unittest.TestCase):
    def setUp(self):
        self.h = _load_handlers()
        self.h.CallbackQuery = FakeCallback
        Calls.cards = []
        self.state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))

    def test_pick_redraws_client_card_with_other_sub(self):
        cb = self.h.AdminUserEditorCallback(action="users_kpu_pick", user_id=7, data="rb")
        _with_card(self.h, self.h.handle_panel_pick(FakeCallback(), cb, None, self.state))
        self.assertEqual(Calls.cards, [(7, "rb", True, 900)])

    def test_back_returns_to_client_card_with_same_sub(self):
        run(self.state.set_state(self.h.PanelUserState.waiting_value))
        cb = self.h.PanelUserCallback(action="back", user_id=7, key_ref="rb")
        _with_card(self.h, self.h.handle_panel_back(FakeCallback(), cb, None, self.state))
        self.assertEqual(Calls.cards, [(7, "rb", True, 900)])
        self.assertIsNone(run(self.state.get_state()))

    def test_saved_input_returns_to_client_card_with_same_sub(self):
        base = self.h.PanelUserCallback(action="", user_id=7, key_ref="rb")
        run(self.state.set_state(self.h.PanelUserState.waiting_value))
        run(self.state.update_data({"kpu_field": "tag", "kpu_base": base.pack()}))

        async def set_field(*a, **k):
            return {}

        self.h.service.set_field = set_field
        _with_card(self.h, self.h.handle_field_input(FakeMessage("VIP"), None, self.state))
        self.assertEqual(Calls.cards, [(7, "rb", False, None)])


@unittest.skipIf(CallbackData is None, "aiogram не установлен")
class TransferInputTest(unittest.TestCase):
    def setUp(self):
        self.h = _load_handlers()
        Calls.users_by_tg, Calls.transferred, Calls.transfer_result = {}, [], True
        self.state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))
        base = self.h.PanelUserCallback(action="", user_id=1, key_ref="ref")
        run(self.state.set_state(self.h.PanelUserState.waiting_value))
        run(self.state.update_data({"kpu_field": "tg", "kpu_base": base.pack()}))

    def _input(self, text):
        message = FakeMessage(text)
        run(self.h.handle_field_input(message, None, self.state))
        return message, run(self.state.get_data())

    def test_unknown_client_is_refused_before_confirm(self):
        message, data = self._input("777")
        self.assertIn("нет в базе бота", message.sent[-1][0])
        self.assertIsNotNone(message.sent[-1][1].get("reply_markup"))
        self.assertNotIn("kpu_dst", data)

    def test_same_owner_is_refused(self):
        Calls.users_by_tg[777] = types.SimpleNamespace(id=1)
        message, data = self._input("777")
        self.assertIn("уже у этого клиента", message.sent[-1][0])
        self.assertNotIn("kpu_dst", data)

    def test_known_client_gets_confirm_screen(self):
        Calls.users_by_tg[777] = types.SimpleNamespace(id=5)
        message, data = self._input("777")
        self.assertEqual(data["kpu_dst"], 5)
        text, kwargs = message.sent[-1]
        self.assertIn("mail@x", text)
        self.assertIn("от 42 к 777", text)
        buttons = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
        self.assertTrue(any(":mv_ok:" in b for b in buttons), buttons)

    def test_bad_number_keeps_waiting(self):
        message, data = self._input("abc")
        self.assertIn("положительное число", message.sent[-1][0])
        self.assertEqual(run(self.state.get_state()), self.h.PanelUserState.waiting_value.state)
        # Из ожидания должен быть выход прямо из ответа с ошибкой, а не только из старого сообщения выше.
        self.assertIsNotNone(message.sent[-1][1].get("reply_markup"))


@unittest.skipIf(CallbackData is None, "aiogram не установлен")
class FieldInputTest(unittest.TestCase):
    """Сбой панели не оставляет ожидание ввода: иначе следующий текст админа лёг бы в поле."""

    def test_panel_failure_clears_waiting(self):
        h = _load_handlers()
        state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))
        base = h.PanelUserCallback(action="", user_id=1, key_ref="ref")
        run(state.set_state(h.PanelUserState.waiting_value))
        run(state.update_data({"kpu_field": "tag", "kpu_base": base.pack()}))

        async def set_field(*a, **k):
            raise h.PanelUserError("Панель не приняла правку")

        h.service.set_field = set_field
        message = FakeMessage("VIP")
        run(h.handle_field_input(message, None, state))
        self.assertIn("не приняла", message.sent[-1][0])
        self.assertIsNone(run(state.get_state()))


class FakeCallback:
    """Нужен isinstance(..., CallbackQuery) в обработчике — подменяем класс в модуле."""

    def __init__(self):
        self.answers = []
        self.from_user = types.SimpleNamespace(id=900)
        self.message = None

    async def answer(self, text=None, show_alert=False, **k):
        self.answers.append((text, show_alert))


@unittest.skipIf(CallbackData is None, "aiogram не установлен")
class TransferConfirmTest(unittest.TestCase):
    def setUp(self):
        self.h = _load_handlers()
        self.h.CallbackQuery = FakeCallback
        Calls.transferred = []
        self.shown = []

        async def show(event, session, state, user_id, key_ref, notice=None):
            self.shown.append((user_id, key_ref))

        self.h._show_client_card = show
        self.state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))
        self.cb = self.h.PanelUserCallback(action="mv_ok", user_id=1, key_ref="ref")

    def _confirm(self):
        callback = FakeCallback()
        run(self.h.handle_transfer_confirm(callback, self.cb, None, self.state))
        return callback

    def test_panel_failure_is_loud(self):
        Calls.transfer_result = False
        run(self.state.update_data({"kpu_dst": 5}))
        callback = self._confirm()
        self.assertEqual(Calls.transferred, [("cid-1", 5, 900)])
        text, alert = callback.answers[-1]
        self.assertTrue(alert)
        self.assertIn("панель", text)
        self.assertEqual(self.shown, [(5, "ref")])

    def test_success_opens_card_of_new_owner(self):
        Calls.transfer_result = True
        run(self.state.update_data({"kpu_dst": 5}))
        callback = self._confirm()
        self.assertFalse(callback.answers[-1][1])
        self.assertEqual(self.shown, [(5, "ref")])

    def test_stale_confirm_does_not_transfer(self):
        callback = self._confirm()
        self.assertEqual(Calls.transferred, [])
        self.assertTrue(callback.answers[-1][1])


if __name__ == "__main__":
    unittest.main()
