import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_password
from app.models import User

log = logging.getLogger(__name__)


def ensure_admin_user(db: Session) -> None:
    """Create the single user from env on first start. Never overwrites an existing user."""
    if db.scalar(select(func.count()).select_from(User)):
        return
    s = get_settings()
    if not (s.admin_email and s.admin_password):
        log.warning("No user exists and MHSQUARE_ADMIN_EMAIL/PASSWORD not set; login impossible.")
        return
    if s.admin_password.startswith("change-me"):
        log.warning("MHSQUARE_ADMIN_PASSWORD is the placeholder; refusing to create user.")
        return
    db.add(User(email=s.admin_email.strip().lower(), password_hash=hash_password(s.admin_password)))
    db.commit()
    log.info("Created admin user %s", s.admin_email)
