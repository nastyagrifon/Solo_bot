from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.executor import run_cpu
from database.identity_sessions import delete_other_sessions
from database.models import Identity
from logger import logger
from utils.cpu_tasks import hash_password


async def ensure_web_admin(session: AsyncSession) -> None:
    """Создаёт или обновляет веб-администратора по учётным данным из настроек."""
    from settings import config

    login = (getattr(config, "WEB_ADMIN_LOGIN", None) or "").strip()
    password = getattr(config, "WEB_ADMIN_PASSWORD", None) or ""

    if not (login and password):
        result = await session.execute(select(Identity).where(Identity.is_admin.is_(True)))
        has_admin = result.scalars().first() is not None
        if not has_admin:
            logger.warning(
                "[web-admin] Нет web-админа и WEB_ADMIN_LOGIN/WEB_ADMIN_PASSWORD "
                "не заданы в config.py. Сайт будет недоступен до создания админа."
            )
        return

    email = login.lower()
    result = await session.execute(select(Identity).where(Identity.email == email))
    identity = result.scalar_one_or_none()
    password_hash = await run_cpu(hash_password, password)
    if identity is None:
        identity = Identity(
            email=email,
            password_hash=password_hash,
            is_admin=True,
            onboarding_stage="done",
        )
        session.add(identity)
        logger.info("[Site:Auth] Создан админский аккаунт сайта {}", email)
    else:
        # Почта могла быть зарегистрирована раньше чужим человеком: его старые
        # сессии не должны стать админскими вместе с повышением.
        if not identity.is_admin:
            await delete_other_sessions(session, identity_id=identity.id, keep_token_hash="")
        identity.password_hash = password_hash
        identity.is_admin = True
        if identity.onboarding_completed_at is None and identity.onboarding_stage != "done":
            identity.onboarding_stage = "done"
        logger.info("[Site:Auth] Пароль админского аккаунта сайта {} синхронизирован", email)
