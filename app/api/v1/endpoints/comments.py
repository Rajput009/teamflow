import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_membership, get_db
from app.models import Membership, User
from app.schemas.common import Page
from app.schemas.validators import NotBlankStr
from app.services.comment_service import CommentService

router = APIRouter(tags=["comments"])


def get_comment_service(session: AsyncSession = Depends(get_db)) -> CommentService:
    return CommentService(session)


class CommentCreate(BaseModel):
    content: NotBlankStr = Field(min_length=1, max_length=5000)


class CommentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_id: uuid.UUID
    author_id: uuid.UUID
    author_email: str | None = None
    author_full_name: str | None = None
    content: str
    created_at: datetime


@router.get("/tasks/{task_id}/comments", response_model=Page[CommentResponse])
async def list_comments(
    task_id: uuid.UUID,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: CommentService = Depends(get_comment_service),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
) -> Page[CommentResponse]:
    actor, membership = context
    rows, total = await service.list_for_task(
        actor=actor,
        actor_membership=membership,
        task_id=task_id,
        page=page,
        limit=limit,
    )
    items = [
        CommentResponse(
            id=c.id,
            task_id=c.task_id,
            author_id=c.author_id,
            author_email=u.email,
            author_full_name=u.full_name,
            content=c.content,
            created_at=c.created_at,
        )
        for c, u in rows
    ]
    return Page[CommentResponse].build(items=items, total=total, page=page, limit=limit)


@router.post(
    "/tasks/{task_id}/comments",
    response_model=CommentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_comment(
    task_id: uuid.UUID,
    payload: CommentCreate,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: CommentService = Depends(get_comment_service),
) -> CommentResponse:
    actor, membership = context
    comment = await service.add(
        actor=actor,
        actor_membership=membership,
        task_id=task_id,
        content=payload.content,
    )
    return CommentResponse(
        id=comment.id,
        task_id=comment.task_id,
        author_id=comment.author_id,
        author_email=actor.email,
        author_full_name=actor.full_name,
        content=comment.content,
        created_at=comment.created_at,
    )


@router.delete("/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_comment(
    comment_id: uuid.UUID,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: CommentService = Depends(get_comment_service),
) -> None:
    actor, membership = context
    await service.delete(actor=actor, actor_membership=membership, comment_id=comment_id)
