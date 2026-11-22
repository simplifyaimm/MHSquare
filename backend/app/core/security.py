"""Password hashing, server-side sessions, CSRF, login rate limiting."""

import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import AuthSession, User

SESSION_COOKIE = "mh_session"
CSRF_COOKIE = "mh_csrf"
CSRF_HEADER = "X-CSRF-Token"

_hasher = PasswordHasher()
# Used to equalise timing when the email is unknown.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        verify_password(_DUMMY_HASH, password)
        return None
    if not verify_password(user.password_hash, password):
        return None
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        db.commit()
    return user


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(
    db: Session, user: User, max_age_s: int, user_agent: str | None
) -> tuple[str, AuthSession]:
    token = secrets.token_urlsafe(32)
    sess = AuthSession(
        user_id=user.id,
        token_hash=_token_hash(token),
        csrf_token=secrets.token_urlsafe(32),
        user_agent=(user_agent or "")[:512],
        expires_at=datetime.now(UTC) + timedelta(seconds=max_age_s),
    )
    db.add(sess)
    # opportunistic cleanup of expired sessions
    db.execute(delete(AuthSession).where(AuthSession.expires_at < datetime.now(UTC)))
    db.commit()
    return token, sess


def lookup_session(db: Session, token: str | None) -> AuthSession | None:
    if not token:
        return None
    sess = db.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
    if sess is None:
        return None
    expires = sess.expires_at
    if expires.tzinfo is None:  # sqlite drops tz
        expires = expires.replace(tzinfo=UTC)
    if expires < datetime.now(UTC):
        db.delete(sess)
        db.commit()
        return None
    return sess


def delete_session(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
        db.commit()


def csrf_matches(expected: str, provided: str | None) -> bool:
    return bool(provided) and hmac.compare_digest(expected, provided or "")


class LoginRateLimiter:
    """Sliding-window limiter on failed logins, keyed by client IP.

    In-process is enough: single user, single API process (see docs/decisions.md).
    """

    def __init__(self, limit: int, window_s: int):
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        q = self._hits[key]
        while q and now - q[0] > self.window_s:
            q.popleft()
        return q

    def retry_after(self, key: str) -> int:
        """Seconds until another attempt is allowed (0 = allowed now)."""
        now = time.monotonic()
        with self._lock:
            q = self._prune(key, now)
            if len(q) < self.limit:
                return 0
            return max(1, int(self.window_s - (now - q[0])) + 1)

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._hits[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)
