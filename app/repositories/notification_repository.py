import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification


class NotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_recipient(
        self,
        recipient_id: uuid.UUID,
        *,
        unread_only: bool = False,
        page: int = 1,
        limit: int = 20,
    ) -> tuple[list[Notification], int]:
        conditions = [Notification.recipient_id == recipient_id]
        if unread_only:
            conditions.append(Notification.read_at.is_(None))

        total = await self._session.scalar(
            select(func.count()).select_from(Notification).where(*conditions)
        )
        stmt = (
            select(Notification)
            .where(*conditions)
            .order_by(Notification.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        return (
            list((await self._session.scalars(stmt)).all()),
            int(total or 0),
        )

    async def get_for_recipient(
        self, notification_id: uuid.UUID, recipient_id: uuid.UUID
    ) -> Notification | None:
        """Scoped by recipient — another user's notification id is a 404,
        never a 403 (existence doesn't leak)."""
        stmt = select(Notification).where(
            Notification.id == notification_id,
            Notification.recipient_id == recipient_id,
        )
        return await self._session.scalar(stmt)

    async def mark_read(self, notification: Notification) -> None:
        if notification.read_at is None:
            notification.read_at = datetime.now(UTC)
            await self._session.flush()

    async def mark_all_read(self, recipient_id: uuid.UUID) -> None:
        await self._session.execute(
            update(Notification)
            .where(
                Notification.recipient_id == recipient_id,
                Notification.read_at.is_(None),
            )
            .values(read_at=datetime.now(UTC))
        )
