import os
import sys

from settings import cache_config, config


# Слоты A/B: второй процесс ядра поднимается рядом на своём порту, конфиг общий.
if os.environ.get("SLOT_PORT"):
    config.WEBAPP_PORT = int(os.environ["SLOT_PORT"])
if os.environ.get("SLOT_API_PORT"):
    config.API_PORT = int(os.environ["SLOT_API_PORT"])


sys.modules.setdefault("config", config)
sys.modules.setdefault("core.cache_config", cache_config)

from settings import buttons, texts


sys.modules.setdefault("handlers.texts", texts)
sys.modules.setdefault("handlers.buttons", buttons)
