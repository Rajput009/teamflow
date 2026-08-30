from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    EmailAlreadyExistsError,
    ForbiddenError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
)
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models import User
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.user_repository import UserRepository

# Upper bound on simultaneously live sessions per user. Each successful
# login/refresh evicts the oldest sessions beyond this limit.
MAX_ACTIVE_REFRESH_TOKENS = 10


class AuthService:
    """Business rules for authentication.

    Notice what is NOT here: HTTP. Services raise domain exceptions and let
    the API layer translate them into responses.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = UserRepository(session)
        self._refresh_tokens = RefreshTokenRepository(session)

    async def register(self, *, email: str, password: str, full_name: str) -> tuple[User, str, str]:
        email = email.strip().lower()

        if await self._users.exists_by_email(email):
            raise EmailAlreadyExistsError()

        # Hash BEFORE any insert attempt — plaintext never touches persistence.
        user = await self._users.create(
            email=email,
            hashed_password=hash_password(password),
            full_name=full_name.strip(),
        )

        access_token, refresh_token = await self._issue_tokens(user)
        return user, access_token, refresh_token

    async def login(self, *, email: str, password: str) -> tuple[User, str, str]:
        email = email.strip().lower()
        user = await self._users.get_by_email(email)

        # Same error whether the email exists or the password is wrong —
        # otherwise this endpoint becomes a tool for enumerating registered
        # emails.
        if user is None:
            # Burn comparable CPU time anyway so response timing doesn't leak
            # whether the account exists (hashing ~50ms, no-hash lookup ~1ms).
            hash_password(password)
            raise InvalidCredentialsError()

        if not verify_password(password, user.hashed_password):
            raise InvalidCredentialsError()

        if not user.is_active:
            raise ForbiddenError(message="This account has been deactivated.")

        access_token, refresh_token = await self._issue_tokens(user)
        return user, access_token, refresh_token

    async def refresh(self, *, raw_refresh_token: str) -> tuple[User, str, str]:
        """Exchange a refresh token for a new pair.

        - Rotation: the presented token is revoked on every successful use.
        - Reuse detection: presenting an ALREADY-revoked token means either a
          bug or a stolen credential being replayed — respond by revoking
          every token the user owns. The attacker (and the legitimate user)
          must re-authenticate.
        """
        now = datetime.now(UTC)
        token = await self._refresh_tokens.get_by_hash(
            hash_refresh_token(raw_refresh_token)
        )

        if token is None or token.expires_at < now:
            raise InvalidRefreshTokenError()

        if token.revoked_at is not None:
            # Reuse of an already-rotated token = theft response: revoke
            # EVERYTHING, then fail. The revocation must survive this failed
            # request, so it is committed HERE — at the source — because the
            # get_db dependency rolls back on any exception by design.
            await self._refresh_tokens.revoke_all_for_user(token.user_id, now)
            await self._session.commit()
            raise InvalidRefreshTokenError()

        user = await self._users.get_by_id(token.user_id)
        if user is None or not user.is_active:
            # Deactivated users must not be able to mint new tokens even
            # with a valid refresh token in hand. Same explicit-commit rule:
            # the security response outlives the error.
            await self._refresh_tokens.revoke_all_for_user(token.user_id, now)
            await self._session.commit()
            raise InvalidRefreshTokenError()

        await self._refresh_tokens.revoke(token, now)

        access_token = create_access_token(user.id)
        raw_new, new_hash, new_expiry = generate_refresh_token()
        await self._refresh_tokens.create(
            user_id=user.id, token_hash=new_hash, expires_at=new_expiry
        )
        return user, access_token, raw_new

    async def logout(self, *, raw_refresh_token: str) -> None:
        """Revoke one refresh token. Idempotent: revoking an unknown,
        expired, or already-revoked token still succeeds — logout must never
        fail from the client's perspective."""
        now = datetime.now(UTC)
        token = await self._refresh_tokens.get_by_hash(
            hash_refresh_token(raw_refresh_token)
        )
        if token is not None and token.revoked_at is None:
            await self._refresh_tokens.revoke(token, now)

    async def _issue_tokens(self, user: User) -> tuple[str, str]:
        now = datetime.now(UTC)
        await self._refresh_tokens.enforce_session_cap(
            user.id, now=now, max_active=MAX_ACTIVE_REFRESH_TOKENS
        )
        access_token = create_access_token(user.id)
        raw_refresh, token_hash, expires_at = generate_refresh_token()
        await self._refresh_tokens.create(
            user_id=user.id, token_hash=token_hash, expires_at=expires_at
        )
        return access_token, raw_refresh
