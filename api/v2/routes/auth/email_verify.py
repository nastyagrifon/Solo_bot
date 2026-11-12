import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import (
    BaseModel,
    Field as PydanticField,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_identity_token
from api.v2.routes.auth._common import _client_ip
from database import identities as idb
from mail import send_email_verify_code_email, smtp_configured
from utils.web_email_codes import email_verify_codes as verify_util


router = APIRouter()


class VerifyEmailRequest(BaseModel):
    code: str = PydanticField(..., min_length=1, max_length=10)


class SetEmailRequest(BaseModel):
    email: str = PydanticField(..., min_length=3, max_length=255)


@router.post("/set-email")
async def set_email(
    body: SetEmailRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    """Указать или сменить email без подтверждения.

    Адрес сохраняется сразу и помечается неподтверждённым: человек видит его
    в профиле с красной плашкой и подтверждает отдельным шагом. Смена уже
    подтверждённого адреса тоже снимает подтверждение — подтверждён не адрес
    вообще, а конкретный адрес.
    """
    from sqlalchemy import update

    from database.identities import get_identity_by_email
    from database.models import Identity as IdentityModel
    from utils.disposable_emails import is_disposable_email
    from utils.web_email_codes import normalize_email

    email_norm = normalize_email(body.email)
    if not email_norm:
        raise HTTPException(status_code=400, detail="Укажите корректный email")
    if is_disposable_email(email_norm):
        raise HTTPException(status_code=400, detail="Одноразовые email-адреса не поддерживаются")

    tekushchiy = (identity.email or "").strip().lower()
    if tekushchiy == email_norm:
        return {"ok": True, "email": email_norm, "email_verified": bool(getattr(identity, "email_verified", False))}

    chuzhoy = await get_identity_by_email(session, email_norm)
    if chuzhoy is not None and chuzhoy.id != identity.id:
        # Занятость адреса не расписываем: ответ один и тот же, кем бы он ни
        # был занят. Разбор таких случаев идёт через поддержку.
        raise HTTPException(
            status_code=409,
            detail="Не удалось указать этот адрес. Напишите в поддержку — разберёмся.",
        )

    await session.execute(
        update(IdentityModel).where(IdentityModel.id == identity.id).values(email=email_norm, email_verified=False)
    )
    return {"ok": True, "email": email_norm, "email_verified": False}


@router.post("/send-verify-code")
async def send_email_verify_code(
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    """Отправить код подтверждения email. Требует авторизации."""
    if not smtp_configured():
        raise HTTPException(status_code=503, detail="Почтовый сервер не настроен")
    email = (identity.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email не привязан к аккаунту")
    if getattr(identity, "email_verified", False):
        return {"ok": True, "detail": "Email уже подтверждён"}
    if not await verify_util.redis_ready():
        raise HTTPException(status_code=503, detail="Сервис временно недоступен")
    ip = _client_ip(request)
    if not await verify_util.try_consume_ip_budget(ip):
        raise HTTPException(status_code=429, detail="Слишком много запросов, попробуйте позже")
    if not await verify_util.try_consume_email_send_budget(email):
        raise HTTPException(status_code=429, detail="Слишком много запросов на этот email")
    if not await verify_util.try_acquire_cooldown(email):
        raise HTTPException(status_code=429, detail="Подождите минуту перед повторной отправкой")
    code = f"{secrets.randbelow(900000) + 100000}"
    await verify_util.store_code(email, code)
    try:
        await send_email_verify_code_email(email, code)
    except Exception:
        await verify_util.delete_code(email)
        raise HTTPException(status_code=503, detail="Не удалось отправить письмо")
    return {"ok": True}


@router.post("/verify-email")
async def verify_email(
    body: VerifyEmailRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    """Подтвердить email по коду."""
    email = (identity.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email не привязан к аккаунту")
    if getattr(identity, "email_verified", False):
        return {"ok": True, "detail": "Email уже подтверждён"}
    if not await verify_util.try_consume_verify_budget(email):
        raise HTTPException(status_code=429, detail="Слишком много попыток, попробуйте позже")
    if not await verify_util.verify_and_consume_code(email, body.code.strip()):
        raise HTTPException(status_code=400, detail="Неверный или просроченный код")
    if await idb.mark_identity_email_verified(session, identity.id, email) is None:
        raise HTTPException(status_code=409, detail="Email изменился. Запросите новый код подтверждения")
    return {"ok": True}
