__all__ = (
    "router",
    "create_payment_link",
    "register_payment_creator",
    "PaymentLinkRequest",
    "PaymentLinkResult",
)

from aiogram import Router

from core.lazy_routers import LazyRouters
from services.payments.payment_links import (
    PaymentLinkRequest,
    PaymentLinkResult,
    create_payment_link,
    register_payment_creator,
)
from services.payments.providers import get_providers, set_router_loader
from settings.config import PROVIDERS_ENABLED

from .fast_payment_flow import router as fast_payment_flow_router
from .gifts import router as gift_router
from .pay import router as pay_router


router = Router(name="payments_main_router")

#: Кассы: модуль → флаги, при любом из которых она нужна. Импорт — только включённых;
#: включённая в админке на ходу подключается при построении меню оплаты.
provider_routers = LazyRouters(router, package=__name__, label="Payments")
provider_routers.add(".yookassa", "YOOKASSA")
provider_routers.add(".yoomoney", "YOOMONEY")
provider_routers.add(".robokassa", "ROBOKASSA")
provider_routers.add(".freekassa.freekassa_pay", "FREEKASSA")
provider_routers.add(".cryptobot", "CRYPTOBOT")
provider_routers.add(".stars", "STARS")
provider_routers.add(".kassai", "KASSAI_CARDS", "KASSAI_SBP")
provider_routers.add(".heleket", "HELEKET")
provider_routers.add(".wata", "WATA_RU", "WATA_INT")
provider_routers.add(".paritypay", "PARITYPAY_SBP")
provider_routers.add(".overpay", "OVERPAY_CARDS", "OVERPAY_SBP")
provider_routers.add(".platega", "PLATEGA_SBP", "PLATEGA_CARDS", "PLATEGA_INT", "PLATEGA_CRYPTO")
provider_routers.add(".tribute", "TRIBUTE")

PROVIDERS = get_providers(PROVIDERS_ENABLED)
provider_routers.boot(name for name, cfg in PROVIDERS.items() if cfg.get("enabled"))
set_router_loader(provider_routers.ensure)

router.include_router(gift_router)
router.include_router(pay_router)
router.include_router(fast_payment_flow_router)

# Анти-тампер и bootstrap-валидация лицензии: запускаются последними, когда пакет gifts
# и все платёжные провайдеры полностью загружены (иначе циклический импорт gifts↔yookassa).
from .gifts import runtime as _gifts_runtime  # noqa: E402,F401
