from api.v2.routes.auth.oauth import make_oauth_router
from database import identities as idb


def _parse_userinfo(info):
    return str(info.get("id") or "").strip(), (info.get("default_email") or "").strip().lower() or None


router = make_oauth_router(
    name="yandex",
    log_name="Yandex",
    human="Яндекс",
    not_configured_detail="Вход через Яндекс не настроен на этом сервере",
    nonce_cookie="y_oauth_nonce",
    auth_endpoint="https://oauth.yandex.ru/authorize",
    token_endpoint="https://oauth.yandex.ru/token",
    userinfo_endpoint="https://login.yandex.ru/info",
    authorize_params={"state": None, "force_confirm": "yes"},
    token_sends_redirect_uri=False,
    userinfo_auth_scheme="OAuth",
    userinfo_params={"format": "json"},
    parse_userinfo=_parse_userinfo,
    get_identity=lambda session, sub, email: idb.get_or_create_identity_for_yandex(
        session, yandex_sub=sub, email=email
    ),
)
