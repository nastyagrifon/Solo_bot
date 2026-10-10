import re

from datetime import datetime
from email.message import EmailMessage
from html import escape as html_escape

import aiosmtplib

from logger import logger
from settings.config import (
    EMAIL_FROM,
    EMAIL_SMTP_HOST,
    EMAIL_SMTP_PASSWORD,
    EMAIL_SMTP_PORT,
    EMAIL_SMTP_USER,
    PROJECT_NAME,
)


_SMTP_TIMEOUT_SEC = 30.0
_SMTP_VALIDATE_CERTS = True


def smtp_configured() -> bool:
    return bool(EMAIL_SMTP_HOST and (EMAIL_FROM or EMAIL_SMTP_USER))


def _get_email_template(key: str, default: str) -> str:
    """Читает шаблон из WEB_CONFIG (настраиваемый админом), fallback на default."""
    try:
        from core.settings.web_config import WEB_CONFIG

        val = WEB_CONFIG.get(key)
        return str(val).strip() if val else default
    except Exception:
        return default


def _render(template: str, **kwargs: str) -> str:
    try:
        return template.format(**kwargs)
    except (KeyError, ValueError):
        return template


def _apply_sender(msg: EmailMessage) -> None:
    addr = EMAIL_FROM or EMAIL_SMTP_USER
    name = _get_email_template("EMAIL_FROM_NAME", PROJECT_NAME)
    msg["From"] = f"{name} <{addr}>"
    reply_to = _get_email_template("EMAIL_REPLY_TO", "").strip()
    if reply_to:
        msg["Reply-To"] = reply_to


def _smtp_kwargs() -> dict:
    kwargs: dict = {
        "hostname": EMAIL_SMTP_HOST,
        "port": EMAIL_SMTP_PORT,
        "username": EMAIL_SMTP_USER or None,
        "password": EMAIL_SMTP_PASSWORD or None,
        "timeout": _SMTP_TIMEOUT_SEC,
        "validate_certs": _SMTP_VALIDATE_CERTS,
    }
    if EMAIL_SMTP_PORT == 465:
        kwargs["use_tls"] = True
        kwargs["start_tls"] = False
    else:
        kwargs["use_tls"] = False
        kwargs["start_tls"] = True
    return kwargs


async def send_login_code_email(to_addr: str, code: str) -> None:
    if not smtp_configured():
        raise RuntimeError("smtp_not_configured")
    project = PROJECT_NAME
    subject = _render(
        _get_email_template("EMAIL_LOGIN_SUBJECT", "{project}: код для входа"), project=project, code=code
    )
    body = _render(_get_email_template("EMAIL_LOGIN_BODY", "Код для входа: {code}"), project=project, code=code)
    await _send_notice(to_addr, subject, body, "кода входа", code=code)


async def send_support_reply_email(to_addr: str, ticket_ref: str, reply: str) -> None:
    if not smtp_configured():
        raise RuntimeError("smtp_not_configured")
    project = PROJECT_NAME
    subject = _render(
        _get_email_template("EMAIL_SUPPORT_REPLY_SUBJECT", "{project}: ответ поддержки"),
        project=project,
        ref=ticket_ref,
    )
    body = _render(
        _get_email_template(
            "EMAIL_SUPPORT_REPLY_BODY",
            "Поддержка ответила по вашему обращению {ref}:\n\n{reply}\n\nОткройте личный кабинет, чтобы продолжить диалог.",
        ),
        project=project,
        ref=ticket_ref,
        reply=reply,
    )
    await _send_notice(to_addr, subject, body, "ответа поддержки")


async def send_password_reset_code_email(to_addr: str, code: str) -> None:
    if not smtp_configured():
        raise RuntimeError("smtp_not_configured")
    project = PROJECT_NAME
    subject = _render(_get_email_template("EMAIL_RESET_SUBJECT", "{project}: сброс пароля"), project=project, code=code)
    body = _render(_get_email_template("EMAIL_RESET_BODY", "Код для сброса пароля: {code}"), project=project, code=code)
    await _send_notice(to_addr, subject, body, "кода сброса пароля", code=code)


async def send_email_verify_code_email(to_addr: str, code: str) -> None:
    if not smtp_configured():
        raise RuntimeError("smtp_not_configured")
    project = PROJECT_NAME
    subject = _render(
        _get_email_template("EMAIL_VERIFY_SUBJECT", "{project}: подтверждение email"), project=project, code=code
    )
    body = _render(
        _get_email_template("EMAIL_VERIFY_BODY", "Код подтверждения email: {code}"), project=project, code=code
    )
    await _send_notice(to_addr, subject, body, "кода подтверждения email", code=code)


# Каркас письма. Тёмный по умолчанию, светлая тема — медиазапросом; разметка
# на таблицах, потому что почтовые клиентыflex и grid не понимают. Цвета
# фирменные, из логотипа. Макет утверждён владельцем 11.10.2026.
DEFAULT_BROADCAST_TEMPLATE = (
    '<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    '<meta name="color-scheme" content="dark light">'
    "<style>body{margin:0;padding:0;}"
    "@media (prefers-color-scheme: light){"
    ".bg{background:#f3f0fa!important}.card{background:#ffffff!important}"
    ".tx{color:#140d26!important}.mt{color:#6b6080!important}"
    ".box{background:#f3f0fa!important}.foot{background:#f7f5fc!important}}"
    "</style></head>"
    '<body class="bg" style="margin:0;padding:28px 12px;background:#1a1030;'
    "font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;\">"
    "{preheader}"
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">'
    '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
    'style="max-width:600px;width:100%;">'
    '<tr><td style="padding:0 4px 18px;">{logo}'
    '<span class="tx" style="color:#ffffff;font-size:19px;font-weight:bold;'
    'vertical-align:middle;">{project}</span></td></tr>'
    '<tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="card" '
    'style="background:#241a46;border-radius:16px;overflow:hidden;">'
    "{image}"
    '<tr><td class="mt" style="padding:30px 30px 4px;color:#a89ec4;font-size:15px;'
    'line-height:1.6;">{content}</td></tr>'
    "{code}"
    "{cta}"
    '<tr><td class="foot" style="background:#1f1640;padding:18px 30px;'
    'color:#8a7fa6;font-size:13px;">© {year} {project}</td></tr>'
    "</table></td></tr></table></td></tr></table></body></html>"
)


def _preheader_block(body: str) -> str:
    """Первая строка письма, которую почтовый клиент показывает в списке."""
    first = html_escape(body.strip().split("\n", 1)[0])[:120]
    return (
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{first}</div>'
        if first
        else ""
    )


def _logo_block() -> str:
    """Логотип в шапке письма. Адрес берётся из настроек, пусто — только текст."""
    url = _get_email_template("EMAIL_LOGO_URL", "").strip()
    if not url:
        return ""
    return (
        f'<img src="{html_escape(url)}" width="32" height="32" alt="" '
        f'style="display:inline-block;vertical-align:middle;border:0;'
        f'border-radius:8px;margin-right:10px;">'
    )


def _code_block(code: str | None) -> str:
    """Код отдельной плашкой: его ищут глазами, а не читают в абзаце."""
    value = (code or "").strip()
    if not value:
        return ""
    hint = _get_email_template("EMAIL_CODE_HINT", "Никому не сообщайте этот код")
    return (
        '<tr><td style="padding:18px 30px 4px;">'
        '<table role="presentation" width="100%" class="box" '
        'style="background:#2c1c55;border-radius:12px;"><tr>'
        '<td align="center" style="padding:22px;">'
        f'<div class="tx" style="font-size:36px;font-weight:bold;letter-spacing:8px;'
        f'color:#ffffff;">{html_escape(value)}</div>'
        f'<div class="mt" style="color:#a89ec4;font-size:13px;padding-top:8px;">'
        f"{html_escape(hint)}</div>"
        "</td></tr></table></td></tr>"
    )


def _html_to_text(html: str) -> str:
    text = re.sub(r'<tg-emoji emoji-id="[^"]*">([^<]*)</tg-emoji>', r"\1", html)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"</p>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&nbsp;", " ")
    return text.strip()


def _telegram_html_to_email(html: str) -> str:
    body = re.sub(r'<tg-emoji emoji-id="[^"]*">([^<]*)</tg-emoji>', r"\1", html)
    body = body.replace("\r\n", "\n").replace("\n", "<br>")
    return body


def _site_url() -> str:
    """Адрес кабинета, если веб включён. Нужен для кнопки в письме."""
    try:
        from core.settings.web_config import get_site_url, is_web_enabled

        return get_site_url() if is_web_enabled() else ""
    except Exception:
        return ""


def _body_without_code(body: str, code: str | None) -> str:
    """Убрать из HTML-части строки, где код уже напечатан словами.

    Код показывается отдельной плашкой, и вторая его копия в абзаце выглядит
    опечаткой. Текстовая часть письма при этом остаётся полной: в ней плашек
    нет, и код из неё убирать нельзя.
    """
    value = (code or "").strip()
    if not value:
        return body
    kept = [line for line in body.splitlines() if value not in line]
    text = "\n".join(kept).strip()
    return text or _get_email_template("EMAIL_CODE_LEAD", "Введите код ниже.")


def build_notice_email(body: str, code: str | None = None) -> str:
    """HTML служебного письма — тем же каркасом, что и рассылки.

    Письма с кодами уходили голым текстом: «Код для привязки email: 334997».
    Шапка, карточка и подвал у проекта уже есть, незачем держать два вида
    писем. Текст берётся как есть и экранируется: это не разметка, а строка
    из настроек, куда админ может написать что угодно.
    """
    project = PROJECT_NAME
    site_url = _site_url()
    cta_block = (
        f'<tr><td style="padding:26px 30px 30px;"><a href="{html_escape(site_url)}" '
        f'style="display:inline-block;background:#7656c8;color:#ffffff;text-decoration:none;'
        f'padding:13px 24px;border-radius:8px;font-size:14px;font-weight:bold;">'
        f'Открыть личный кабинет</a></td></tr>'
        if site_url
        else ""
    )
    # Превью письма строим без кода: иначе он виден в списке писем и на
    # заблокированном экране телефона.
    lead = _body_without_code(body, code)
    template = _get_email_template("EMAIL_NOTICE_TEMPLATE", DEFAULT_BROADCAST_TEMPLATE)
    return (
        template.replace("{project}", html_escape(project))
        .replace("{image}", "")
        .replace("{cta}", cta_block)
        .replace("{site_url}", html_escape(site_url))
        .replace("{year}", str(datetime.now().year))
        .replace("{content}", html_escape(lead).replace("\n", "<br>"))
        .replace("{preheader}", _preheader_block(lead))
        .replace("{logo}", _logo_block())
        .replace("{code}", _code_block(code))
    )


async def _send_notice(to_addr: str, subject: str, body: str, what: str, code: str | None = None) -> None:
    """Служебное письмо: текстовая часть и html одним и тем же содержимым."""
    msg = EmailMessage()
    msg["Subject"] = subject
    _apply_sender(msg)
    msg["To"] = to_addr
    msg.set_content(body)
    msg.add_alternative(build_notice_email(body, code), subtype="html")
    try:
        await aiosmtplib.send(msg, **_smtp_kwargs())
    except Exception as exc:
        logger.warning(f"[SMTP] Отправка {what} на {to_addr} не удалась: {exc}")
        raise


def build_broadcast_email(text_html: str, image_url: str | None = None) -> tuple[str, str, str]:
    project = PROJECT_NAME
    plain = _html_to_text(text_html)
    first_line = plain.split("\n", 1)[0].strip()
    title = first_line or project
    subject = _render(_get_email_template("EMAIL_BROADCAST_SUBJECT", "{project}: {title}"), project=project, title=title)

    site_url = ""
    try:
        from core.settings.web_config import get_site_url, is_web_enabled

        site_url = get_site_url() if is_web_enabled() else ""
    except Exception:
        site_url = ""

    image_block = (
        f'<tr><td style="padding:0;"><img src="{html_escape(image_url)}" alt="" '
        f'style="width:100%;display:block;"></td></tr>'
        if image_url
        else ""
    )
    cta_block = (
        f'<tr><td style="padding:26px 30px 30px;"><a href="{html_escape(site_url)}" '
        f'style="display:inline-block;background:#7656c8;color:#ffffff;text-decoration:none;'
        f'padding:13px 24px;border-radius:8px;font-size:14px;font-weight:bold;">'
        f'Открыть личный кабинет</a></td></tr>'
        if site_url
        else ""
    )

    template = _get_email_template("EMAIL_BROADCAST_TEMPLATE", DEFAULT_BROADCAST_TEMPLATE)
    html = (
        template.replace("{project}", html_escape(project))
        .replace("{image}", image_block)
        .replace("{cta}", cta_block)
        .replace("{preheader}", _preheader_block(plain))
        .replace("{logo}", _logo_block())
        .replace("{code}", "")
        .replace("{site_url}", html_escape(site_url))
        .replace("{year}", str(datetime.now().year))
        .replace("{content}", _telegram_html_to_email(text_html))
    )
    return subject, html, plain


async def send_broadcast_email(to_addr: str, subject: str, html_body: str, text_body: str) -> None:
    if not smtp_configured():
        raise RuntimeError("smtp_not_configured")
    msg = EmailMessage()
    msg["Subject"] = subject
    _apply_sender(msg)
    msg["To"] = to_addr
    msg.set_content(text_body or " ")
    msg.add_alternative(html_body, subtype="html")
    await aiosmtplib.send(msg, **_smtp_kwargs())


async def send_email_link_code_email(to_addr: str, code: str) -> None:
    if not smtp_configured():
        raise RuntimeError("smtp_not_configured")
    project = PROJECT_NAME
    subject = _render(
        _get_email_template("EMAIL_LINK_SUBJECT", "{project}: подтверждение привязки email"), project=project, code=code
    )
    body = _render(_get_email_template("EMAIL_LINK_BODY", "Код для привязки email: {code}"), project=project, code=code)
    await _send_notice(to_addr, subject, body, "кода привязки email", code=code)
