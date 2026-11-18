"""Экран устройств ключа в админке: список, блок HWID на прослойке, передача модулю.

Ядро подменено заглушками; клавиатура и обработчики — настоящие.
Запуск из корня проекта: ``venv/bin/python -m unittest discover -s tests -v``
"""

import asyncio
import hashlib
import hmac
import importlib.util
import json
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import aiogram  # noqa: F401
except ImportError:
    aiogram = None


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


class Env:
    config = _module("settings.config", HWID_RESET_BUTTON=True)
    module_enabled = False
    devices: list = []
    user_info: dict = {}
    gate_calls: list = []
    deleted: list = []


class Key:
    server_id, client_id, email, remnawave_link, key = "srv", "cid", "mail@x", "https://s/1", None
    tg_id, user_id, tariff_id = 42, 1, None
    current_device_limit, selected_device_limit = 3, None


class UserId(int):
    pass


class TelegramId(int):
    pass


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_all(test):
    async def resolve_admin_key(session, user_id, key_ref):
        return Key if key_ref == "ref" else None

    async def resolve_api_url(session, server_id):
        return "https://panel"

    class Api:
        async def get_user_by_uuid(self, *a, **k):
            return Env.user_info

        async def get_user_hwid_devices(self, *a, **k):
            return Env.devices

        async def delete_user_hwid_device(self, client_id, hwid, **k):
            Env.deleted.append(hwid)
            return True

    async def with_remnawave_api(session, server_id, op, **k):
        return await op(Api())

    async def noop(*a, **k):
        return None

    manager = types.SimpleNamespace(is_enabled=lambda name: Env.module_enabled and name == "devices")
    log = types.SimpleNamespace(warning=lambda *a, **k: None, info=lambda *a, **k: None)
    services = _module("services")
    services.__path__ = []
    stubs = {
        "sqlalchemy": _module("sqlalchemy"),
        "sqlalchemy.ext": _module("sqlalchemy.ext"),
        "sqlalchemy.ext.asyncio": _module("sqlalchemy.ext.asyncio", AsyncSession=object),
        "core": _module("core"),
        "core.bootstrap": _module("core.bootstrap", BUTTONS_CONFIG={}),
        "database": _module("database", get_clusters=noop, get_key_expiry_presets=noop),
        "database.access": _module("database.access"),
        "database.access.resolution": _module("database.access.resolution", UserId=UserId, TelegramId=TelegramId),
        "hooks": _module("hooks"),
        "hooks.hook_buttons": _module("hooks.hook_buttons", insert_hook_buttons=lambda b, h: b),
        "hooks.hooks": _module("hooks.hooks", run_hooks=noop),
        "services": services,
        "services.formatting": _module("services.formatting", format_days=str),
        "services.users_utils": _module(
            "services.users_utils", build_admin_key_ref=lambda c, e: "ref", resolve_admin_key=resolve_admin_key
        ),
        "settings": _module("settings", config=Env.config),
        "settings.buttons": _module("settings.buttons", BACK="Назад"),
        "settings.config": Env.config,
        "utils": _module("utils"),
        "utils.modules_manager": _module("utils.modules_manager", manager=manager),
        "logger": _module("logger", logger=log),
        "filters": _module("filters"),
        "filters.admin": _module("filters.admin", IsAdminFilter=lambda: (lambda *a, **k: True)),
        "panels": _module("panels"),
        "panels.remnawave_runtime": _module(
            "panels.remnawave_runtime",
            with_remnawave_api=with_remnawave_api,
            resolve_remnawave_api_url=resolve_api_url,
            invalidate_remnawave_profile=noop,
        ),
        "handlers": _module("handlers"),
        "handlers.admin": _module("handlers.admin"),
        "handlers.admin.panel": _module("handlers.admin.panel"),
        "handlers.admin.panel.headers": _module(
            "handlers.admin.panel.headers",
            card=lambda *b: "\n".join(b),
            menu_text=lambda *p, **k: "\n".join(p),
            section=lambda t, *lines: "\n".join((t, *lines)),
        ),
        "handlers.admin.panel.keyboard": _module(
            "handlers.admin.panel.keyboard", build_admin_back_btn=lambda *a, **k: None, nav_row=lambda *a, **k: []
        ),
        "handlers.admin.users": _module("handlers.admin.users"),
        "handlers.admin.users.panel_user": _module("handlers.admin.users.panel_user"),
        "handlers.admin.users.panel_user.service": _module(
            "handlers.admin.users.panel_user.service", PanelUserError=Exception, clear_devices=noop
        ),
    }
    stubs["handlers.admin.users"].__path__ = [str(ROOT / "handlers/admin/users")]
    names = [*stubs, "services.hwid_block", "handlers.admin.users.keyboard", "handlers.admin.users.users_hwid"]
    saved = {k: sys.modules.get(k) for k in names}

    def restore():
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    # Заглушки живут весь тест: клиент прослойки и кнопка модуля импортируют лениво.
    test.addCleanup(restore)
    sys.modules.update(stubs)
    gate = _load("services.hwid_block", ROOT / "services/hwid_block.py")
    services.hwid_block = gate
    kb = _load("handlers.admin.users.keyboard", ROOT / "handlers/admin/users/keyboard.py")
    hwid = _load("handlers.admin.users.users_hwid", ROOT / "handlers/admin/users/users_hwid.py")
    return gate, kb, hwid


class FakeResp:
    def __init__(self, status, text):
        self.status, self._text = status, text

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeAiohttp:
    """Подменяет aiohttp: запоминает запрос и отвечает заданным."""

    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.requests = answer or {"ok": True}, error, []
        outer = self

        class Session:
            def __init__(self, timeout=None):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def post(self, url, data=None, headers=None):
                if outer.error:
                    raise outer.error
                outer.requests.append((url, data, headers))
                return FakeResp(200, json.dumps(outer.answer))

        self.module = _module("aiohttp", ClientSession=Session, ClientTimeout=lambda total: total)

    def __enter__(self):
        self.saved = sys.modules.get("aiohttp")
        sys.modules["aiohttp"] = self.module
        return self

    def __exit__(self, *a):
        if self.saved is None:
            sys.modules.pop("aiohttp", None)
        else:
            sys.modules["aiohttp"] = self.saved


class Message:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text, reply_markup=None, **k):
        self.edits.append((text, reply_markup))


class Callback:
    def __init__(self):
        self.message, self.answers = Message(), []
        self.from_user = types.SimpleNamespace(id=7)

    async def answer(self, text=None, **k):
        self.answers.append(text)


def run(coro):
    return asyncio.run(coro)


def configure(on: bool):
    Env.config.HWID_BLOCK_API_URL = "https://gate/api" if on else ""
    Env.config.HWID_BLOCK_API_SECRET = "s3cret" if on else ""


def buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


@unittest.skipIf(aiogram is None, "aiogram не установлен")
class GateClientTest(unittest.TestCase):
    def setUp(self):
        self.gate, _, _ = _load_all(self)
        configure(True)

    def test_sign_is_hmac_over_ts_nonce_body(self):
        expected = hmac.new(b"k", b"17\nab\n{}", hashlib.sha256).hexdigest()
        self.assertEqual(self.gate.sign("k", "17", "ab", b"{}"), expected)

    def test_request_signed_over_exact_body(self):
        with FakeAiohttp() as fake:
            run(self.gate.block("HW-1", username="mail@x"))
        url, body, headers = fake.requests[0]
        self.assertEqual(url, "https://gate/api")
        payload = f"{headers['X-Bot-Ts']}\n{headers['X-Bot-Nonce']}\n".encode() + body
        self.assertEqual(headers["X-Bot-Sig"], hmac.new(b"s3cret", payload, hashlib.sha256).hexdigest())
        self.assertEqual(len(headers["X-Bot-Nonce"]), 32)
        sent = json.loads(body)
        self.assertEqual((sent["op"], sent["hwid"], sent["by"]), ("hwid_block", "HW-1", "admin"))

    def test_not_configured(self):
        configure(False)
        self.assertFalse(self.gate.configured())
        self.assertIsNone(run(self.gate.status_or_none(["a"])))
        with self.assertRaises(self.gate.HwidBlockError):
            run(self.gate.unblock("a"))

    def test_gate_down_is_soft(self):
        with FakeAiohttp(error=OSError("down")):
            self.assertIsNone(run(self.gate.status_or_none(["a"])))

    def test_refusal_is_error(self):
        with FakeAiohttp(answer={"ok": False, "error": "bad"}):
            with self.assertRaises(self.gate.HwidBlockError):
                run(self.gate.block("a"))


DEVICES = [
    {"hwid": "AAA-1", "deviceModel": "Pixel 8", "platform": "Android", "osVersion": "14",
     "userAgent": "Happ/4.6.0/Android", "updatedAt": "2026-11-17T10:00:00.000Z"},
    {"hwid": "BBB-2", "deviceModel": "iPhone", "platform": "iOS", "userAgent": "INCY/3.8.6"},
]


@unittest.skipIf(aiogram is None, "aiogram не установлен")
class DevicesScreenTest(unittest.TestCase):
    def setUp(self):
        self.gate, self.kb, self.h = _load_all(self)
        Env.devices, Env.user_info, Env.deleted = [dict(d) for d in DEVICES], {"hwidDeviceLimit": 5}, []
        configure(False)

    def _render(self, user_id=1):
        cb = Callback()
        run(self.h._render_admin_devices(cb, None, "ref", UserId(user_id), 0))
        return cb.message.edits[-1]

    def test_find_device_only_in_this_key(self):
        own = self.h.fingerprint("BBB-2")
        self.assertEqual(self.h.find_device(DEVICES, own), (1, DEVICES[1]))
        self.assertIsNone(self.h.find_device(DEVICES, self.h.fingerprint("FOREIGN")))

    def test_foreign_fingerprint_is_not_blocked(self):
        configure(True)
        cb = Callback()
        data = types.SimpleNamespace(data="ref|" + self.h.fingerprint("FOREIGN"), user_id=UserId(1))
        with FakeAiohttp() as fake:
            run(self.h._device_action(cb, data, None, self.h._block))
        self.assertEqual(fake.requests, [])
        self.assertEqual(cb.answers, ["Устройство не найдено"])

    def test_unbind_by_fingerprint(self):
        cb = Callback()
        data = types.SimpleNamespace(data="ref|" + self.h.fingerprint("BBB-2"), user_id=UserId(1))
        run(self.h._device_action(cb, data, None, self.h._unbind))
        self.assertEqual(Env.deleted, ["BBB-2"])

    def test_block_failure_is_text_not_crash(self):
        configure(True)
        cb = Callback()
        data = types.SimpleNamespace(data="ref|" + self.h.fingerprint("AAA-1"), user_id=UserId(1))
        with FakeAiohttp(error=OSError("down")):
            run(self.h._device_action(cb, data, None, self.h._block))
        self.assertIn("Прослойка подписки не ответила", cb.answers[0])

    def test_no_gate_no_block_buttons(self):
        text, markup = self._render()
        actions = [b.callback_data for b in buttons(markup)]
        self.assertFalse([a for a in actions if "users_hwid_bl" in a or "users_hwid_ub" in a])
        self.assertEqual(sum("users_hwid_rm" in a for a in actions), 2)
        self.assertIn("Pixel 8 · Android 14 · Happ 4.6.0", text)
        self.assertIn("Лимит тарифа: 3", text)
        self.assertIn("Лимит панели: 5", text)

    def test_gate_marks_blocked(self):
        configure(True)
        with FakeAiohttp(answer={"ok": True, "blocked": {"AAA-1": "bot_admin"}}):
            text, markup = self._render()
        actions = [b.callback_data for b in buttons(markup)]
        self.assertIn("🔒 админ", text)
        self.assertEqual(sum("users_hwid_ub" in a for a in actions), 1)
        self.assertEqual(sum("users_hwid_bl" in a for a in actions), 1)

    def test_manual_block_has_no_unblock(self):
        self.assertIsNone(self.h.block_action({"hwid": "A"}, {"A": "manual"}))
        self.assertEqual(self.h.block_action({"hwid": "A"}, {"A": "bot"}), "users_hwid_ub")
        self.assertIsNone(self.h.block_action({"hwid": "A"}, None))
        self.assertIsNone(self.h.block_action({"hwid": "with space"}, {}))

    def test_callback_data_fits_64_bytes(self):
        configure(True)
        Env.devices = [{"hwid": "X" * 189 + "%02d" % i, "deviceModel": "M" * 40} for i in range(12)]
        with FakeAiohttp(answer={"ok": True, "blocked": {}}):
            _, markup = self._render(user_id=9_999_999_999)
        datas = [b.callback_data for b in buttons(markup)]
        self.assertTrue(any("users_hwid_bl" in d for d in datas))
        for d in datas:
            self.assertLessEqual(len(d.encode()), 64, d)
        tg_markup = self.kb.build_hwid_menu_kb(
            "a" * 12, TelegramId(9_999_999_999), rows=[(1, "m", "f" * 12, "users_hwid_ub")]
        )
        for b in buttons(tg_markup):
            self.assertLessEqual(len(b.callback_data.encode()), 64, b.callback_data)


@unittest.skipIf(aiogram is None, "aiogram не установлен")
class HandOverToModuleTest(unittest.TestCase):
    def setUp(self):
        _, self.kb, _ = _load_all(self)

    def _devices_button(self, tg_id):
        markup = self.kb.build_key_edit_kb({"user_id": 1, "client_id": "c", "tg_id": tg_id}, "mail@x", key_ref="ref")
        return next(b for b in buttons(markup) if b.text == "💻 Устройства").callback_data

    def tearDown(self):
        Env.module_enabled = False

    def test_module_enabled_leads_to_module(self):
        Env.module_enabled = True
        self.assertEqual(self._devices_button(42), "dev_acard|42")

    def test_module_button_does_not_depend_on_client_reset_flag(self):
        Env.module_enabled = True
        self.kb.HWID_RESET_BUTTON = False
        try:
            self.assertEqual(self._devices_button(42), "dev_acard|42")
        finally:
            self.kb.HWID_RESET_BUTTON = True

    def test_module_off_core_screen(self):
        self.assertIn("users_hwid_menu", self._devices_button(42))

    def test_no_tg_id_core_screen(self):
        Env.module_enabled = True
        self.assertIn("users_hwid_menu", self._devices_button(None))

    def test_key_card_has_no_panel_fields(self):
        # Поля панели живут в карточке клиента; в карточке ключа — только «Устройства».
        markup = self.kb.build_key_edit_kb(
            {"user_id": 1, "client_id": "c", "tg_id": 42}, "mail@x", key_ref="ref", show_subscription_keys=True
        )
        self.assertFalse([b.callback_data for b in buttons(markup) if "users_kpu_" in b.callback_data])


@unittest.skipIf(aiogram is None, "aiogram не установлен")
class PermissionsTest(unittest.TestCase):
    def test_device_actions_need_key_devices(self):
        from filters.permissions import PERM_KEY_DEVICES

        stubs = {
            "core": _module("core"),
            "core.settings": _module("core.settings"),
            "core.settings.management_config": _module("core.settings.management_config", MANAGEMENT_CONFIG={}),
        }
        saved = {k: sys.modules.get(k) for k in stubs}
        sys.modules.update(stubs)
        try:
            spec = importlib.util.spec_from_file_location("admin_actions", ROOT / "filters/admin_actions.py")
            acl = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(acl)
        finally:
            for k, v in saved.items():
                sys.modules.pop(k, None) if v is None else sys.modules.__setitem__(k, v)
        self.assertEqual(acl.callback_action_permissions("dev_acard|42"), (PERM_KEY_DEVICES,))
        for action in ("users_hwid_rm", "users_hwid_bl", "users_hwid_ub"):
            with self.subTest(action=action):
                self.assertEqual(
                    acl.callback_action_permissions(f"admin_user:{action}:u1:ref|abc:0"), (PERM_KEY_DEVICES,)
                )


if __name__ == "__main__":
    unittest.main()
