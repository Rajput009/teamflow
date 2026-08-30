import uuid
from collections.abc import AsyncGenerator

import jwt as pyjwt
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AccountDisabledError,
    InvalidAccessTokenError,
    NoOrganizationError,
    NotAuthenticatedError,
)
from app.core.security import decode_access_token
from app.db.session import async_session_factory
from app.models import Membership, User
from app.repositories.membership_repository import MembershipRepository
from app.repositories.user_repository import UserRepository

# tokenUrl points Swagger UI's "Authorize" button at our login endpoint.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield one database session per request.

    FastAPI resolves this dependency before the endpoint runs and runs the
    remaining steps after it returns. Two exit paths:

    - success       -> commit (normal case)
    - ANY exception -> rollback + re-raise

    There is deliberately NO commit-on-domain-error rule: rolling back on
    every failure means no future service can accidentally persist half-done
    work by flushing before raising. The one feature that NEEDS writes to
    survive an error — the refresh-token theft response in AuthService.refresh
    — commits explicitly at its source, where the intent is visible and
    reviewable instead of implicit in a global exception policy.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_db),
) -> User:
    """Authenticate the caller. Attach to any endpoint that requires a user.

    Dependencies compose: this itself depends on get_db, so the user lookup
    shares the request's single session/transaction.
    """
    if token is None:
        raise NotAuthenticatedError()

    try:
        payload = decode_access_token(token)
    except pyjwt.PyJWTError:
        # Covers expired, tampered signature, malformed — one response shape
        # for all of them, so attackers learn nothing about WHICH check failed.
        raise InvalidAccessTokenError() from None

    if payload.get("type") != "access":
        # A refresh token must never work as an access token.
        raise InvalidAccessTokenError()

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise InvalidAccessTokenError() from None

    user = await UserRepository(session).get_by_id(user_id)

    if user is None:
        # Token is cryptographically valid but the user vanished (deleted).
        raise InvalidAccessTokenError()

    if not user.is_active:
        # Deactivation takes effect immediately even for unexpired tokens.
        raise AccountDisabledError()

    return user


async def get_current_membership(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> tuple[User, Membership]:
    """Resolve the caller's organization context.

    This is Layer 1 of the RBAC chain (05-rbac-multi-tenancy.md): everything
    downstream receives BOTH the user and their role in the active org.
    When multi-org support lands, only THIS function changes.
    """
    membership = await MembershipRepository(session).get_first_for_user(current_user.id)
    if membership is None:
        raise NoOrganizationError()
    return current_user, membership
