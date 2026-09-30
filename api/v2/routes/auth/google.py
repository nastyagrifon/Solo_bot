from api.v2.routes.auth.oauth import make_oauth_router
from database import identities as idb


def _parse_userinfo(info):
    email = (info.get("email") or "").strip().lower() or None
    email_verified = bool(info.get("email_verified"))
    return str(info.get("sub") or "").strip(), email if (email and email_verified) else None


router = make_oauth_router(
    name="google",
    log_name="Google",
    human="Google",
    not_configured_detail="Google Sign-In не настроен на этом сервере",
    nonce_cookie="g_oauth_nonce",
    auth_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
    token_endpoint="https://oauth2.googleapis.com/token",
    userinfo_endpoint="https://openidconnect.googleapis.com/v1/userinfo",
    authorize_params={
        "scope": "openid email profile",
        "state": None,
        "access_type": "online",
        "prompt": "select_account",
    },
    token_sends_redirect_uri=True,
    userinfo_auth_scheme="Bearer",
    userinfo_params=None,
    parse_userinfo=_parse_userinfo,
    get_identity=lambda session, sub, email: idb.get_or_create_identity_for_google(
        session, google_sub=sub, email=email
    ),
)
