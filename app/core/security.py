"""Identity resolution for every request.

Two modes, selected by `AUTH_REQUIRED`:

- **off (default)** — a bearer token is still honoured when present, and `X-User-Id` remains as the
  zero-setup development identity. This keeps the project runnable with a single curl command.
- **on** — only a valid bearer token identifies a caller. `X-User-Id` is ignored entirely, because a
  header a client can set freely is not authentication.

Collections, Reels, jobs, and every other resource are scoped by the resolved identity either way,
so turning authentication on does not change any authorization logic — only who the caller is.
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.passwords import InvalidToken, read_token
from app.db.database import get_db
from app.models.user import User

DEVELOPMENT_USER = "local-user"


def get_current_user_id(
    authorization: str = Header(default=""),
    x_user_id: str = Header(default=DEVELOPMENT_USER),
    db: Session = Depends(get_db),
) -> str:
    settings = get_settings()
    token = _bearer_token(authorization)

    if token:
        return _subject_from_token(token, db)

    if settings.auth_required:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = x_user_id.strip()
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="X-User-Id must not be blank")
    return user_id


def get_authenticated_user(
    authorization: str = Header(default=""), db: Session = Depends(get_db)
) -> User:
    """For endpoints that need the account itself, not just its scoping identity."""
    token = _bearer_token(authorization)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    subject = _subject_from_token(token, db)
    user = db.get(User, int(subject.removeprefix("user:")))
    if user is None or not user.is_active:
        raise _invalid_credentials()
    return user


def _bearer_token(authorization: str) -> str:
    scheme, _, value = authorization.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def _subject_from_token(token: str, db: Session) -> str:
    try:
        subject = read_token(token, secret=get_settings().resolved_secret_key)
    except InvalidToken as error:
        raise _invalid_credentials(str(error)) from error
    # A token outliving its account must stop working immediately, so the account is re-checked.
    if subject.startswith("user:"):
        user = db.get(User, int(subject.removeprefix("user:")))
        if user is None or not user.is_active:
            raise _invalid_credentials("Account is no longer active")
    return subject


def _invalid_credentials(detail: str = "Invalid or expired credentials") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )
