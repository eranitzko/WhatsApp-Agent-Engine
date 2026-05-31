"""Admin panel authentication — password check and JWT lifecycle."""

from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import settings

logger = logging.getLogger(__name__)

_ALGORITHM = "HS256"
_TOKEN_EXPIRE_SECONDS = 86400  # 24 hours

_bearer = HTTPBearer(auto_error=False)


class AdminAuthError(Exception):
    """Raised when password is wrong or admin UI is not configured."""


def _jwt_secret() -> str:
    """Derive a stable JWT signing secret from the admin password."""
    return hashlib.sha256(settings.admin_ui_password.encode()).hexdigest()


def create_token(password: str) -> str:
    """Verify password and return a signed JWT. Raises AdminAuthError on failure."""
    if not settings.admin_ui_password:
        raise AdminAuthError("ADMIN_UI_PASSWORD is not configured")
    if not hmac.compare_digest(password, settings.admin_ui_password):
        raise AdminAuthError("Invalid password")
    import time
    payload = {"sub": "admin", "exp": int(time.time()) + _TOKEN_EXPIRE_SECONDS}
    return jwt.encode(payload, _jwt_secret(), algorithm=_ALGORITHM)


def verify_token(token: str) -> bool:
    """Return True if the token is valid and not expired, False otherwise."""
    if not token:
        return False
    try:
        jwt.decode(token, _jwt_secret(), algorithms=[_ALGORITHM])
        return True
    except JWTError:
        return False


def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    """FastAPI dependency — raises 401 if token is missing or invalid."""
    token = credentials.credentials if credentials else ""
    if not verify_token(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
