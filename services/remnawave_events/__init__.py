"""Приём вебхуков Remnawave: подпись, разбор конверта, раздача модулям.

Панель шлёт все события на один WEBHOOK_URL. Ядро принимает их здесь и
раздаёт модулям хуком ``remnawave_event`` — модулю не нужен свой приёмник,
и слушателей может быть сколько угодно.

Подписка в модуле::

    from hooks.hooks import register_hook

    @register_hook("remnawave_event")
    async def on_panel_event(event: str, data: dict, **kwargs):
        if event == "user.expired":
            ...
"""

from .envelope import extract_data, extract_event, signature_valid

__all__ = ["extract_data", "extract_event", "signature_valid"]
