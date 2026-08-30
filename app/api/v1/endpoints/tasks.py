import math
import uuid

from fastapi import APIRouter, Depends, Query
from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_membership, get_db
from app.models import Membership, TaskPriority, TaskStatus, User
from app.schemas.task import (
    AssignRequest,
    TaskCreate,
    TaskPage,
    TaskResponse,
    TaskUpdate,
)
from app.services.task_service import TaskService

router = APIRouter(tags=["tasks"])


def get_task_service(session: AsyncSession = Depends(get_db)) -> TaskService:
    return TaskService(session)


@router.post(
    "/projects/{project_id}/tasks",
    response_model=TaskResponse,
    status_code=http_status.HTTP_201_CREATED,
)
async def create_task(
    project_id: uuid.UUID,
    payload: TaskCreate,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: TaskService = Depends(get_task_service),
) -> TaskResponse:
    actor, membership = context
    task = await service.create(
        actor=actor,
        actor_membership=membership,
        project_id=project_id,
        title=payload.title,
        description=payload.description,
        status=payload.status,
        priority=payload.priority,
        due_date=payload.due_date,
        assigned_to_id=payload.assigned_to_id,
    )
    return TaskResponse.model_validate(task)


@router.get("/projects/{project_id}/tasks", response_model=TaskPage)
async def list_tasks(
    project_id: uuid.UUID,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: TaskService = Depends(get_task_service),
    status_filter: TaskStatus | None = Query(default=None, alias="status"),
    priority: TaskPriority | None = Query(default=None),
    assigned_to: uuid.UUID | None = Query(default=None),
    search: str | None = Query(default=None, max_length=100),
    ordering: str = Query(default="-created_at", max_length=32),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> TaskPage:
    actor, membership = context
    items, total = await service.list_for_project(
        actor=actor,
        actor_membership=membership,
        project_id=project_id,
        status=status_filter,
        priority=priority,
        assigned_to=assigned_to,
        search=search,
        ordering=ordering,
        page=page,
        limit=limit,
    )
    return TaskPage(
        items=[TaskResponse.model_validate(t) for t in items],
        total=total,
        page=page,
        limit=limit,
        pages=math.ceil(total / limit),
    )


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: uuid.UUID,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: TaskService = Depends(get_task_service),
) -> TaskResponse:
    actor, membership = context
    task = await service.get_in_org(
        actor=actor, actor_membership=membership, task_id=task_id
    )
    return TaskResponse.model_validate(task)


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: uuid.UUID,
    payload: TaskUpdate,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: TaskService = Depends(get_task_service),
) -> TaskResponse:
    actor, membership = context
    task = await service.update(
        actor=actor,
        actor_membership=membership,
        task_id=task_id,
        # exclude_unset: absent key = untouched, explicit null = clear
        fields=payload.model_dump(exclude_unset=True),
    )
    return TaskResponse.model_validate(task)


@router.delete("/tasks/{task_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: uuid.UUID,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: TaskService = Depends(get_task_service),
) -> None:
    actor, membership = context
    await service.delete(actor=actor, actor_membership=membership, task_id=task_id)


@router.post("/tasks/{task_id}/assign", response_model=TaskResponse)
async def assign_task(
    task_id: uuid.UUID,
    payload: AssignRequest,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: TaskService = Depends(get_task_service),
) -> TaskResponse:
    actor, membership = context
    task = await service.assign(
        actor=actor,
        actor_membership=membership,
        task_id=task_id,
        target_user_id=payload.user_id,
    )
    return TaskResponse.model_validate(task)
