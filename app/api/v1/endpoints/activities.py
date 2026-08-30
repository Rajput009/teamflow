import math
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_membership, get_db
from app.models import Membership, User
from app.services.activity_service import ActivityService

router = APIRouter(tags=["activities"])


def get_activity_service(session: AsyncSession = Depends(get_db)) -> ActivityService:
    return ActivityService(session)


class ActivityResponse(BaseModel):
    id: uuid.UUID
    action: str
    entity_type: str
    entity_id: uuid.UUID
    project_id: uuid.UUID | None
    task_id: uuid.UUID | None
    old_value: dict | None
    new_value: dict | None
    actor_id: uuid.UUID
    actor_email: str
    actor_full_name: str
    created_at: datetime


class ActivityPage(BaseModel):
    items: list[ActivityResponse]
    total: int
    page: int
    limit: int
    pages: int


def _to_page(
    rows: list[tuple], total: int, page: int, limit: int
) -> ActivityPage:
    return ActivityPage(
        items=[
            ActivityResponse(
                id=a.id,
                action=a.action,
                entity_type=a.entity_type,
                entity_id=a.entity_id,
                project_id=a.project_id,
                task_id=a.task_id,
                old_value=a.old_value,
                new_value=a.new_value,
                actor_id=a.actor_id,
                actor_email=u.email,
                actor_full_name=u.full_name,
                created_at=a.created_at,
            )
            for a, u in rows
        ],
        total=total,
        page=page,
        limit=limit,
        pages=math.ceil(total / limit),
    )


@router.get("/activities", response_model=ActivityPage)
async def list_org_activities(
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: ActivityService = Depends(get_activity_service),
    action: str | None = Query(default=None, max_length=100),
    actor_id: uuid.UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> ActivityPage:
    _actor, membership = context
    rows, total = await service.list_org_activities(
        actor_membership=membership,
        action=action,
        actor_id=actor_id,
        page=page,
        limit=limit,
    )
    return _to_page(rows, total, page, limit)


@router.get("/projects/{project_id}/activities", response_model=ActivityPage)
async def list_project_activities(
    project_id: uuid.UUID,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: ActivityService = Depends(get_activity_service),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> ActivityPage:
    actor, membership = context
    rows, total = await service.list_project_activities(
        actor=actor,
        actor_membership=membership,
        project_id=project_id,
        page=page,
        limit=limit,
    )
    return _to_page(rows, total, page, limit)
