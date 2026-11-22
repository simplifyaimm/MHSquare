from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import CSRF_HEADER, SESSION_COOKIE, csrf_matches, lookup_session
from app.models import AuthSession, User

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_auth_session(request: Request, db: Session = Depends(get_db)) -> AuthSession:
    sess = lookup_session(db, request.cookies.get(SESSION_COOKIE))
    if sess is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    if request.method not in SAFE_METHODS and not csrf_matches(
        sess.csrf_token, request.headers.get(CSRF_HEADER)
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token missing or invalid")
    return sess


def get_current_user(
    sess: AuthSession = Depends(get_auth_session), db: Session = Depends(get_db)
) -> User:
    user = db.get(User, sess.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return user
