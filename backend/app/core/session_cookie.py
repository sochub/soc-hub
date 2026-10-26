"""Browser sessions ride in an HttpOnly cookie, so page scripts (an XSS included) can never read the token.

- API clients keep using `Authorization: Bearer`; the header wins when both are present.
- The refresh token rides in its own HttpOnly cookie (`sochub_refresh`), scoped to the auth endpoints (refresh,
  logout), so no data request ever carries it.
- The SPA sends `X-Requested-With` on every call. A cookie-authenticated unsafe request without it is refused
  (CSRF defence on top of SameSite=Strict), and a response never puts the token in its body for the SPA or for
  a cookie-authenticated caller, so a script running in the page cannot mint itself a readable token.
"""
from typing import Optional

from cryptography.fernet import InvalidToken

from fastapi import HTTPException, Request, Response, status

from app.core.config import settings
from app.utils.crypto import decrypt, encrypt

COOKIE = "sochub_session"
REFRESH_COOKIE = "sochub_refresh"
REFRESH_PATH = "/api/v1/auth"  # refresh and logout (which revokes by it) only
CSRF_HEADER = "x-requested-with"
# Guest-portal sessions: their own cookie, sent only to the guest API, so a guest credential never reaches a
# member route and the member cookie is never read by a guest route (the JWT purpose claim is checked too).
GUEST_COOKIE = "sochub_guest"
GUEST_PATH = "/api/v1/guest"
_SAFE = frozenset({"GET", "HEAD", "OPTIONS"})


def set_session(response: Response, token: str) -> None:
    response.set_cookie(COOKIE, token, max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60, path="/api",
                        httponly=True, samesite="strict", secure=settings.PUBLIC_BASE_URL.startswith("https"))


def clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/api", httponly=True, samesite="strict",
                           secure=settings.PUBLIC_BASE_URL.startswith("https"))


def _secure() -> bool:
    return settings.PUBLIC_BASE_URL.startswith("https")


def set_refresh(response: Response, refresh_token: str) -> None:
    # Sealed with the server key: the cookie (browser profile, proxies, crash dumps) never holds the raw token.
    response.set_cookie(REFRESH_COOKIE, encrypt(refresh_token), max_age=settings.REFRESH_IDLE_DAYS * 86400,
                        path=REFRESH_PATH, httponly=True, samesite="strict", secure=_secure())


def read_refresh(request: Request) -> Optional[str]:
    """The refresh token from its sealed cookie; a missing, tampered or pre-sealing cookie reads as None
    (the browser then signs in again)."""
    sealed = request.cookies.get(REFRESH_COOKIE)
    if not sealed:
        return None
    try:
        return decrypt(sealed)
    except (InvalidToken, ValueError):
        return None


def clear_refresh(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_PATH, httponly=True, samesite="strict", secure=_secure())


def set_guest(response: Response, token: str, max_age: int) -> None:
    response.set_cookie(GUEST_COOKIE, token, max_age=max_age, path=GUEST_PATH,
                        httponly=True, samesite="strict", secure=_secure())


def clear_guest(response: Response) -> None:
    response.delete_cookie(GUEST_COOKIE, path=GUEST_PATH, httponly=True, samesite="strict", secure=_secure())


def is_api_client(request: Request) -> bool:
    """Neither the SPA (CSRF header) nor a cookie-authenticated caller: tokens may go in the body."""
    return getattr(request.state, "auth_via", None) != "cookie" and CSRF_HEADER not in request.headers


def request_token(request: Request, header_token: Optional[str], cookie: str = COOKIE) -> Optional[str]:
    """The bearer header if sent, else the session cookie (enforcing the CSRF header on unsafe methods)."""
    if header_token:
        request.state.auth_via = "bearer"
        return header_token
    token = request.cookies.get(cookie)
    if not token:
        return None
    if request.method not in _SAFE and CSRF_HEADER not in request.headers:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing X-Requested-With header")
    request.state.auth_via = "cookie"
    return token


def issue(request: Request, response: Response, token: str, refresh_token: Optional[str] = None) -> dict:
    """Start or refresh the browser session; hand the tokens themselves only to non-browser API clients.
    Without ``refresh_token`` the client's refresh cookie/secret is left as it is."""
    set_session(response, token)
    if refresh_token:
        set_refresh(response, refresh_token)
    body = {"token_type": "bearer"}
    if is_api_client(request):
        body["access_token"] = token
        if refresh_token:
            body["refresh_token"] = refresh_token
    return body
