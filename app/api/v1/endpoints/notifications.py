import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.exceptions import NotFoundError
from app.models import Notification, User
from app.repositories.notification_repository import NotificationRepository
from app.schemas.common import Page

router = APIRouter(prefix="/notifications", tags=["notifications"])


def get_notification_repo(session: AsyncSession = Depends(get_db)) -> NotificationRepository:
    return NotificationRepository(session)


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    payload: dict
    read_at: datetime | None
    created_at: datetime


def _to_page(
    items: list[Notification], total: int, page: int, limit: int
) -> Page[NotificationResponse]:
    return Page[NotificationResponse].build(
        items=[NotificationResponse.model_validate(n) for n in items],
        total=total,
        page=page,
        limit=limit,
    )


@router.get("", response_model=Page[NotificationResponse])
async def list_notifications(
    current_user: User = Depends(get_current_user),
    repo: NotificationRepository = Depends(get_notification_repo),
    unread_only: bool = Query(default=False, alias="unread"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> Page[NotificationResponse]:
    items, total = await repo.list_for_recipient(
        current_user.id, unread_only=unread_only, page=page, limit=limit
    )
    return _to_page(items, total, page, limit)


@router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_notification_read(
    notification_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    repo: NotificationRepository = Depends(get_notification_repo),
) -> None:
    notification = await repo.get_for_recipient(notification_id, current_user.id)
    if notification is None:
        raise NotFoundError(message="Notification not found.")
    await repo.mark_read(notification)


@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def mark_all_notifications_read(
    current_user: User = Depends(get_current_user),
    repo: NotificationRepository = Depends(get_notification_repo),
) -> None:
    await repo.mark_all_read(current_user.id)
