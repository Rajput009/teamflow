import uuid
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RefreshToken


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, *, user_id: uuid.UUID, token_hash: str, expires_at: datetime
    ) -> RefreshToken:
        token = RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
        self._session.add(token)
        await self._session.flush()
        return token

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        return await self._session.scalar(stmt)

    async def revoke(self, token: RefreshToken, revoked_at: datetime) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.id == token.id)
            .values(revoked_at=revoked_at)
        )

    async def revoke_all_for_user(self, user_id: uuid.UUID, revoked_at: datetime) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
        )

    async def enforce_session_cap(
        self, user_id: uuid.UUID, *, now: datetime, max_active: int
    ) -> None:
        """Keep at most (max_active - 1) tokens alive so that the new token
        issued right after stays within the cap. Oldest sessions die first —
        a login flood can no longer grow the table without bound."""
        actives = (
            await self._session.scalars(
                select(RefreshToken)
                .where(
                    RefreshToken.user_id == user_id,
                    RefreshToken.revoked_at.is_(None),
                )
                .order_by(RefreshToken.created_at.desc())
            )
        ).all()
        for stale in actives[max_active - 1 :]:
            await self.revoke(stale, now)

    async def delete_expired(self, older_than: datetime) -> None:
        """Housekeeping (called by a periodic job in V3)."""
        await self._session.execute(
            delete(RefreshToken).where(RefreshToken.expires_at < older_than)
        )
