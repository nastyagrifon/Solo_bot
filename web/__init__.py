from aiohttp.web_urldispatcher import UrlDispatcher

from core.readiness import slot_ready
from services.payments.heleket.webhook import heleket_webhook
from services.payments.kassai.webhook import kassai_webhook
from services.payments.overpay.webhook import overpay_webhook
from services.payments.paritypay.webhook import paritypay_webhook
from services.payments.platega.webhook import platega_webhook
from services.payments.wata.webhook import wata_webhook
from services.remnawave_events.webhook import make_handler as make_remnawave_handler
from utils.modules_loader import load_module_webhooks


KASSAI_WEBHOOK_PATH = "/kassai/webhook"
HELEKET_WEBHOOK_PATH = "/heleket/webhook"
WATA_WEBHOOK_PATH = "/wata/webhook"
PARITYPAY_WEBHOOK_PATH = "/paritypay/webhook"
PLATEGA_WEBHOOK_PATH = "/platega/webhook"
OVERPAY_WEBHOOK_PATH = "/overpay/webhook"
REMNAWAVE_WEBHOOK_PATH_DEFAULT = "/remnawave/webhook"


#: Колбэки касс из закрытого ядра: путь (из вики вендора) → (флаг, модуль, обработчик).
#: Ядро регистрирует такой путь при старте только для кассы, включённой в конфиге.
#: Для выключенной ставим посредника: включат кассу в админке на ходу — колбэки
#: пойдут сразу; выключат — посредник остаётся и докатывает оплаты по уже
#: выставленным счетам. Проверка подписи — в обработчике самой кассы.
CORE_PROVIDER_WEBHOOKS = {
    "/yookassa/webhook": ("YOOKASSA", "services.payments.yookassa.webhook", "yookassa_webhook"),
    "/yoomoney/webhook": ("YOOMONEY", "services.payments.yoomoney.webhook", "yoomoney_webhook"),
    "/robokassa/webhook": ("ROBOKASSA", "services.payments.robokassa.webhook", "robokassa_webhook"),
    "/cryptobot/webhook": ("CRYPTOBOT", "services.payments.cryptobot.webhook", "cryptobot_webhook"),
    "/tribute/webhook": ("TRIBUTE", "services.payments.tribute.webhook", "tribute_webhook"),
}


def _lazy_provider_webhook(module_path: str, attr: str):
    """Обработчик кассы, импортируемый при первом колбэке."""
    from importlib import import_module

    async def proxy(request):  # import_module кэширует модуль в sys.modules
        return await getattr(import_module(module_path), attr)(request)

    proxy.__name__ = f"lazy_{attr}"
    return proxy


def _register_core_provider_webhooks(router: UrlDispatcher) -> None:
    from settings.config import PROVIDERS_ENABLED

    for path, (flag, module_path, attr) in CORE_PROVIDER_WEBHOOKS.items():
        if PROVIDERS_ENABLED.get(flag):
            continue  # включена в конфиге — маршрут регистрирует ядро
        try:
            router.add_post(path, _lazy_provider_webhook(module_path, attr))
        except RuntimeError as e:  # путь уже занят — работает штатный маршрут
            print(f"[Web] {path}: оставлен маршрут ядра ({e})")


async def register_web_routes(router: UrlDispatcher) -> None:
    router.add_get("/slot/ready", slot_ready)
    router.add_post(KASSAI_WEBHOOK_PATH, kassai_webhook)
    router.add_post(HELEKET_WEBHOOK_PATH, heleket_webhook)
    router.add_post(WATA_WEBHOOK_PATH, wata_webhook)
    router.add_post(PARITYPAY_WEBHOOK_PATH, paritypay_webhook)
    router.add_post(PLATEGA_WEBHOOK_PATH, platega_webhook)
    router.add_post(OVERPAY_WEBHOOK_PATH, overpay_webhook)
    _register_core_provider_webhooks(router)

    # Настройки необязательные: в конфиге из шаблона их может не быть.
    import settings.config as config

    rw_secret = getattr(config, "REMNAWAVE_WEBHOOK_SECRET", "") or ""
    rw_path = getattr(config, "REMNAWAVE_WEBHOOK_PATH", "") or REMNAWAVE_WEBHOOK_PATH_DEFAULT
    if rw_secret:
        router.add_post(rw_path, make_remnawave_handler(rw_secret))
        print(f"[Web] Зарегистрирован приёмник событий панели: {rw_path}")
    else:
        print("[Web] REMNAWAVE_WEBHOOK_SECRET не задан — приёмник событий панели выключен")

    from core import module_runtime

    router.add_route("*", module_runtime.MODULE_WEB_PREFIX + "/{module}/{tail:.*}", module_runtime.module_web_dispatch)

    try:
        module_webhooks = load_module_webhooks()

        for webhook_data in module_webhooks:
            path = webhook_data.get("path")
            handler = webhook_data.get("handler")
            if path and handler:
                router.add_post(path, handler)
                print(f"[Web] Зарегистрирован вебхук модуля: {path}")
    except Exception as e:
        print(f"[Web] Ошибка при загрузке вебхуков модулей: {e}")
