"""Синтетика экрана обновления. Гонять венвом бота: нужен настоящий aiogram.

Пакет handlers целиком не импортируем — он поднимает активацию вендора
(«Бот не активирован. Код: 1061»). Поэтому собираем ровно те модули, что
нужны: настоящие aiogram и headers, заглушки на router и клавиатуру.
"""
import importlib.util, os, re, sys, types

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


for name in ("handlers", "handlers.admin", "handlers.admin.panel", "handlers.admin.management"):
    pkg = types.ModuleType(name)
    pkg.__path__ = [os.path.join(ROOT, *name.split(".")[1:])] if "." in name else [os.path.join(ROOT, "handlers")]
    sys.modules[name] = pkg

class R:
    def callback_query(self, *a, **k): return lambda f: f
sys.modules["handlers.admin.management"].router = R()

load("handlers.admin.panel.headers", os.path.join(ROOT, "handlers/admin/panel/headers.py"))

kbd = types.ModuleType("handlers.admin.panel.keyboard")
class AdminPanelCallback(CallbackData, prefix="admin_panel"):
    action: str
    page: int
    def __init__(self, /, **data):
        data.setdefault("page", 1)
        super().__init__(**data)
kbd.AdminPanelCallback = AdminPanelCallback
kbd.build_admin_back_btn = lambda action="admin": InlineKeyboardButton(
    text="⬅️ Назад", callback_data=AdminPanelCallback(action=action).pack())
sys.modules["handlers.admin.panel.keyboard"] = kbd

ex = types.ModuleType("core.executor")
ex.run_io = ex.spawn = lambda *a, **k: None
sys.modules["core.executor"] = ex
for name in ("filters.admin", "filters.permissions"):
    m = types.ModuleType(name)
    m.HasPermission = m.IsAdminFilter = lambda *a, **k: None
    m.PERM_MANAGEMENT = "mgmt"
    sys.modules[name] = m
log = types.ModuleType("logger")
class L:
    def __getattr__(self, _): return lambda *a, **k: None
log.logger = L(); sys.modules["logger"] = log

uh = load("handlers.admin.management.update_handler",
         os.path.join(ROOT, "handlers/admin/management/update_handler.py"))

STATUS = """▶ a :3101 active    ready=True venv=22317880cd7d b4c662ff Merge pull request #134 from nastyagrifon/fix/admin-panel-info
  b :3102 inactive  ready=False venv=22317880cd7d ef141303 Merge pull request #133 from nastyagrifon/fix/disarm"""

fails = []
def check(name, cond, got=""):
    print(("  ok  " if cond else " FAIL ") + name + ("" if cond else "  <- " + str(got)[:180]))
    if not cond: fails.append(name)

slots = uh._slots(STATUS)
check("разобраны два слота", len(slots) == 2, slots)
check("имена слотов", [x["name"] for x in slots] == ["a", "b"], slots)
check("активный один и это a", [x["active"] for x in slots] == [True, False], slots)
check("хэш активного", slots[0]["commit"] == "b4c662ff", slots[0])
check("хэш неактивного", slots[1]["commit"] == "ef141303", slots[1])
check("неактивный = b", uh._inactive_slot(STATUS) == "b", uh._inactive_slot(STATUS))
check("пустой статус = нет слота", uh._inactive_slot("") == "", "-")
check("мусорная строка не слот", uh._slots("bash: slotctl: command not found") == [], uh._slots("bash: slotctl: command not found"))

tail = uh._human_tail("22:52:01 поднимаю слот\n22:52:42 ✔ активный слот b\n")
check("хвост без метки времени", tail == "✔ активный слот b", tail)
check("пустой вывод = пустой хвост", uh._human_tail("") == "", "-")

uh._installed_version = lambda: "v6.0 · 16.10 22:51 · b4c662ff (dev)"
uh._slot_date = lambda name: "16.10 22:51" if name == "a" else "15.10 19:04"
text = uh._screen_text(STATUS, "b")

# Внутренности на экран не лезут: админу они ничего не говорят, а экран
# превращается в дамп (Настя, 16.10.2026: «нечитаемое машинное дерьмо»).
LEAKS = ("ready=", "venv=", ":3101", ":3102", ".service", "inactive", "active ")
leaked = [bad for bad in LEAKS if bad in text]
check("внутренности не на экране", not leaked, leaked)
check("дата слота на экране", "16.10 22:51" in text, text[:200])
check("состояние открывает строку", "Работает" in text and "Свободен" in text, text[:260])
check("работающая установка сверху", text.index("Работает") < text.index("Свободен"), "-")
check("про простой не врём", "замолчит" not in text and "перезапустится" not in text, "-")
ALLOWED = {"b", "/b", "blockquote", "/blockquote", "code", "/code", "i", "/i", "u", "/u", "tg-spoiler", "/tg-spoiler"}
tags = set(re.findall(r"<([^>\s]+)[^>]*>", text))
check("теги только разрешённые", tags <= ALLOWED, sorted(tags - ALLOWED))
check("в тексте оба слота", "15.10 19:04" in text and "16.10 22:51" in text, text[:200])
empty = uh._screen_text("", "")
check("экран без слотов не падает", "slotctl" in empty, "-")
# Угловые скобки в тексте Telegram читает как тег и отбрасывает сообщение
# целиком — ветку «слотов нет» проверяем так же строго, как основную.
tags_empty = set(re.findall(r"<([^>\s]+)[^>]*>", empty))
check("теги разрешены и без слотов", tags_empty <= ALLOWED, sorted(tags_empty - ALLOWED))

kb = uh._build_kb("b").export()
flat = [btn.callback_data for row in kb for btn in row]
check("кнопка install", any(c.endswith("update_install:1") for c in flat), flat)
check("кнопка switch", any(c.endswith("update_switch:1") for c in flat), flat)
check("кнопка rollback", any(c.endswith("update_rollback:1") for c in flat), flat)
check("callback_data <= 64 байт", all(len(c.encode()) <= 64 for c in flat), flat)
flat0 = [btn.callback_data for row in uh._build_kb("").export() for btn in row]
check("без слотов нет опасных кнопок", not any("install" in c or "switch" in c for c in flat0), flat0)
conf = [btn.callback_data for row in uh._confirm_kb("update_switch_go").export() for btn in row]
check("подтверждение ведёт на _go", any("update_switch_go" in c for c in conf), conf)
check("подтверждение даёт отмену", any(c.endswith("update:1") for c in conf), conf)

uh.REPORT_DIR = "/tmp"
uh.OWN_SLOT = "b"
own, alien = uh._report_file("b"), uh._report_file("a")
check("отчёты слотов — разные файлы", own != alien, (own, alien))

open(own, "w").write("ok 4242\nslot b up\nCaddy switched\n")
check("отчёт прочитан", uh.take_update_report() == ("ok", 4242, "slot b up\nCaddy switched"), "-")
check("файл удалён после чтения", not os.path.exists(own), "-")
check("нет файла = None", uh.take_update_report() is None, "-")

# Уходящий слот не должен съедать доклад, адресованный пришедшему.
open(alien, "w").write("ok 4242\nчужой отчёт\n")
check("чужой отчёт не читается", uh.take_update_report() is None, "-")
check("чужой отчёт не удалён", os.path.exists(alien), "-")
os.unlink(alien)

open(own, "w").write("garbage\n")
check("битый отчёт = None", uh.take_update_report() is None, "-")
open(own, "w").write("fail 1\nне поднялся\n")
st, who, body = uh.take_update_report()
txt = uh.format_report(st, body)
check("провал помечен", "не удалось" in txt, txt[:100])
ok_txt = uh.format_report("ok", "22:52:01 поднимаю\n22:52:42 ✔ активный слот b\n")
check("успех помечен", "прошло" in ok_txt, ok_txt[:100])
check("успех без простыни вывода", "22:52:01" not in ok_txt, ok_txt[:160])
check("провал показывает вывод", "не поднялся" in txt, txt[:160])
tags = set(re.findall(r"<([^>\s]+)[^>]*>", txt))
check("теги отчёта разрешены", tags <= ALLOWED, sorted(tags - ALLOWED))

captured = {}
class FakeDone:
    returncode = 0
    stderr = b""
uh.subprocess = types.SimpleNamespace(
    run=lambda cmd, **k: (captured.setdefault("cmd", cmd), FakeDone())[1],
    Popen=lambda cmd, **k: captured.setdefault("popen", cmd),
    DEVNULL=-3, PIPE=-1, STDOUT=-2, TimeoutExpired=Exception)
uh.shutil = types.SimpleNamespace(which=lambda n: "/usr/bin/" + n)
uh._launch_detached(["switch"], 4242, "b")
cmd = captured.get("cmd", [])
script = cmd[-1] if cmd else ""
check("отдельный юнит", "--unit" in cmd and "--collect" in cmd, cmd[:6])
check("в скрипте slotctl switch", "slotctl switch" in script, script[:160])
check("отчёт пишется через mv", ".tmp" in script and "mv " in script, script[-120:])
check("id админа в отчёте", "4242" in script, script[:70])
check("отчёт над каталогом проекта", uh.REPORT_DIR != ROOT, uh.REPORT_DIR)
check("в команде адрес целевого слота", ".update_report.b" in script, script[-140:])

print()
print("ИТОГ: " + ("всё зелёное, проверок %d" % (43 - len(fails)) if not fails else "красных %d: %s" % (len(fails), fails)))
sys.exit(1 if fails else 0)
