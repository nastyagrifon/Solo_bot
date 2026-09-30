import hashlib
import hmac
import secrets

from urllib.parse import urlencode

import httpx

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import (
    _is_secure_request,
    bind_identity_actor,
    get_session,
    set_auth_cookie,
    set_is_admin_cookie,
)
from api.shared.oauth_state import STATE_TTL_SECONDS, make_state, verify_state
from api.v2.routes.auth._common import _client_ip, safe_return_path, with_login_marker
from database import identities as idb
from logger import logger


try:
    from settings import config as _config
except ImportError:
    _config = None

_OAUTH_SUCCESS_URI = getattr(_config, "OAUTH_SUCCESS_URI", "/dashboard")


def make_oauth_router(
    *,
    name,
    log_name,
    human,
    not_configured_detail,
    nonce_cookie,
    auth_endpoint,
    token_endpoint,
    userinfo_endpoint,
    authorize_params,
    token_sends_redirect_uri,
    userinfo_auth_scheme,
    userinfo_params,
    parse_userinfo,
    get_identity,
) -> APIRouter:
    """Роутер /{name}/authorize|callback|status для OAuth-провайдера.

    authorize_params: доп. параметры авторизации; ключ "state" (значение None)
    держит место state в URL. parse_userinfo(info) -> (sub, email для БД или None).
    get_identity(session, sub, email) -> Identity.
    """
    client_id = getattr(_config, f"{name.upper()}_CLIENT_ID", "")
    client_secret = getattr(_config, f"{name.upper()}_CLIENT_SECRET", "")
    redirect_uri = getattr(_config, f"{name.upper()}_REDIRECT_URI", "")
    state_key = str(getattr(_config, "API_TOKEN", "") or "").strip() or secrets.token_hex(32)
    state_secret = hashlib.sha256(f"oauth-state:{name}:v1:".encode() + state_key.encode()).hexdigest()

    router = APIRouter()

    def configured() -> bool:
        return bool(client_id and client_secret and redirect_uri)

    @router.get(f"/{name}/authorize", name=f"{name}_authorize")
    async def authorize(
        request: Request,
        return_to: str = Query(default=""),
    ):
        """Начинает OAuth-флоу: редиректит юзера на consent screen провайдера."""
        if not configured():
            raise HTTPException(status_code=503, detail=not_configured_detail)
        safe_return = safe_return_path(return_to, _OAUTH_SUCCESS_URI)
        state, nonce = make_state(state_secret, safe_return)
        params = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", **authorize_params}
        params["state"] = state
        url = f"{auth_endpoint}?{urlencode(params)}"
        logger.info("[Auth] {} authorize: ip={}", log_name, _client_ip(request))
        resp = RedirectResponse(url, status_code=302)
        resp.set_cookie(
            key=nonce_cookie,
            value=nonce,
            max_age=STATE_TTL_SECONDS,
            path="/",
            httponly=True,
            secure=_is_secure_request(request),
            samesite="lax",
        )
        return resp

    @router.get(f"/{name}/callback", name=f"{name}_callback")
    async def callback(
        request: Request,
        response: Response,
        code: str = Query(default=""),
        state: str = Query(default=""),
        error: str = Query(default=""),
        session: AsyncSession = Depends(get_session),
    ):
        """Коллбек провайдера: обмен code → token → userinfo → identity."""
        if not configured():
            raise HTTPException(status_code=503, detail=not_configured_detail)
        if error:
            logger.warning("[Auth] {} callback error: {} ip={}", log_name, error, _client_ip(request))
            return RedirectResponse(f"/login?error={name}_{error}", status_code=302)
        if not code or not state:
            raise HTTPException(status_code=400, detail="Отсутствует code или state")
        verified = verify_state(state_secret, state, _OAUTH_SUCCESS_URI)
        if verified is None:
            logger.warning("[Auth] {} callback: invalid/expired state ip={}", log_name, _client_ip(request))
            raise HTTPException(status_code=400, detail="Неверный или просроченный state")
        return_to, state_nonce = verified
        cookie_nonce = (request.cookies.get(nonce_cookie) or "").strip()
        if not cookie_nonce or not hmac.compare_digest(cookie_nonce, state_nonce):
            logger.warning("[Auth] {} callback: state/cookie nonce mismatch ip={}", log_name, _client_ip(request))
            raise HTTPException(status_code=400, detail="Неверный или просроченный state")

        token_data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        if token_sends_redirect_uri:
            token_data["redirect_uri"] = redirect_uri
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                token_res = await client.post(token_endpoint, data=token_data, headers={"Accept": "application/json"})
            except httpx.HTTPError as e:
                logger.warning("[Auth] {} token exchange network error: {}", log_name, e)
                raise HTTPException(status_code=502, detail=f"Не удалось связаться с {human}") from e
            if token_res.status_code != 200:
                logger.warning(
                    "[Auth] {} token exchange failed: {} {}", log_name, token_res.status_code, token_res.text[:200]
                )
                raise HTTPException(status_code=401, detail=f"{human} отклонил токен")
            token_payload = token_res.json()
            access_token = token_payload.get("access_token")
            if not access_token:
                raise HTTPException(status_code=401, detail=f"{human} не вернул access_token")

            try:
                info_res = await client.get(
                    userinfo_endpoint,
                    headers={"Authorization": f"{userinfo_auth_scheme} {access_token}"},
                    params=userinfo_params,
                )
            except httpx.HTTPError as e:
                logger.warning("[Auth] {} userinfo network error: {}", log_name, e)
                raise HTTPException(status_code=502, detail=f"Не удалось получить профиль {human}") from e

        if info_res.status_code != 200:
            raise HTTPException(status_code=401, detail=f"{human} не вернул профиль")
        sub, email = parse_userinfo(info_res.json())
        if not sub:
            raise HTTPException(status_code=401, detail=f"{human} не вернул идентификатор пользователя")

        identity = await get_identity(session, sub, email)
        await bind_identity_actor(request, session, identity)
        token = await idb.issue_token_for_identity(session, identity, request=request)
        logger.info(
            f"[Auth] Login success: identity={{}}, {name}_sub={{}}, ip={{}}, method={name}",
            identity.id,
            sub,
            _client_ip(request),
        )
        redirect = RedirectResponse(with_login_marker(safe_return_path(return_to, _OAUTH_SUCCESS_URI)), status_code=302)
        redirect.delete_cookie(nonce_cookie, path="/")
        set_auth_cookie(redirect, token, request)
        set_is_admin_cookie(redirect, identity, request)
        return redirect

    @router.get(f"/{name}/status", name=f"{name}_status")
    async def status():
        """Позволяет фронтенду узнать, настроен ли вход через провайдера на этом сервере."""
        return {"enabled": configured()}

    return router
