from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.deps import get_auth_session, get_current_user
from app.core.security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    LoginRateLimiter,
    authenticate,
    create_session,
    delete_session,
)
from app.models import AuthSession, User
from app.schemas.auth import LoginRequest, MeResponse, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

_settings = get_settings()
limiter = LoginRateLimiter(_settings.login_rate_limit, _settings.login_rate_window_s)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _set_cookies(response: Response, token: str, csrf: str) -> None:
    s = get_settings()
    common = {"secure": s.cookie_secure, "samesite": "strict", "max_age": s.session_max_age_s, "path": "/"}
    response.set_cookie(SESSION_COOKIE, token, httponly=True, **common)
    # Readable by same-origin JS so the UI can echo it in the X-CSRF-Token header.
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, **common)


@router.post("/login", response_model=MeResponse)
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    key = _client_key(request)
    wait = limiter.retry_after(key)
    if wait:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many login attempts. Try again in {wait}s.",
            headers={"Retry-After": str(wait)},
        )
    user = authenticate(db, body.email, body.password)
    if user is None:
        limiter.record_failure(key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    limiter.reset(key)
    token, sess = create_session(
        db, user, get_settings().session_max_age_s, request.headers.get("user-agent")
    )
    _set_cookies(response, token, sess.csrf_token)
    return MeResponse(user=UserOut(id=user.id, email=user.email), csrf_token=sess.csrf_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    _: AuthSession = Depends(get_auth_session),
    db: Session = Depends(get_db),
):
    delete_session(db, request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


@router.get("/me", response_model=MeResponse)
def me(sess: AuthSession = Depends(get_auth_session), user: User = Depends(get_current_user)):
    return MeResponse(user=UserOut(id=user.id, email=user.email), csrf_token=sess.csrf_token)
