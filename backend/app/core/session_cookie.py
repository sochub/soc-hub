"""Browser sessions ride in an HttpOnly cookie, so page scripts (an XSS included) can never read the token.

- API clients keep using `Authorization: Bearer`; the header wins when both are present.
- The SPA sends `X-Requested-With` on every call. A cookie-authenticated unsafe request without it is refused
  (CSRF defence on top of SameSite=Strict), and a response never puts the token in its body for the SPA or for
  a cookie-authenticated caller, so a script running in the page cannot mint itself a readable token.
"""
from typing import Optional

from fastapi import HTTPException, Request, Response, status

from app.core.config import settings

COOKIE = "sochub_session"
CSRF_HEADER = "x-requested-with"
_SAFE = frozenset({"GET", "HEAD", "OPTIONS"})


def set_session(response: Response, token: str) -> None:
    response.set_cookie(COOKIE, token, max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60, path="/api",
                        httponly=True, samesite="strict", secure=settings.PUBLIC_BASE_URL.startswith("https"))


def clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/api", httponly=True, samesite="strict",
                           secure=settings.PUBLIC_BASE_URL.startswith("https"))


def request_token(request: Request, header_token: Optional[str]) -> Optional[str]:
    """The bearer header if sent, else the session cookie (enforcing the CSRF header on unsafe methods)."""
    if header_token:
        request.state.auth_via = "bearer"
        return header_token
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    if request.method not in _SAFE and CSRF_HEADER not in request.headers:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing X-Requested-With header")
    request.state.auth_via = "cookie"
    return token


def issue(request: Request, response: Response, token: str) -> dict:
    """Start or refresh the browser session; hand the token itself only to non-browser API clients."""
    set_session(response, token)
    body = {"token_type": "bearer"}
    if getattr(request.state, "auth_via", None) != "cookie" and CSRF_HEADER not in request.headers:
        body["access_token"] = token
    return body
