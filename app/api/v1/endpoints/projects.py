import uuid

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_membership, get_db
from app.models import Membership, User
from app.schemas.common import Page
from app.schemas.project import ProjectCreate, ProjectResponse, ProjectUpdate
from app.services.project_service import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


def get_project_service(session: AsyncSession = Depends(get_db)) -> ProjectService:
    return ProjectService(session)


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: ProjectService = Depends(get_project_service),
) -> ProjectResponse:
    actor, membership = context
    project = await service.create(
        actor=actor,
        actor_membership=membership,
        name=payload.name,
        description=payload.description,
        deadline=payload.deadline,
    )
    return ProjectResponse.model_validate(project)


@router.get("", response_model=Page[ProjectResponse])
async def list_projects(
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: ProjectService = Depends(get_project_service),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> Page[ProjectResponse]:
    actor, membership = context
    items, total = await service.list_for_org(
        actor=actor, actor_membership=membership, page=page, limit=limit
    )
    return Page[ProjectResponse].build(
        items=[ProjectResponse.model_validate(p) for p in items],
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: uuid.UUID,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: ProjectService = Depends(get_project_service),
) -> ProjectResponse:
    actor, membership = context
    project = await service.get_in_org(
        actor=actor, actor_membership=membership, project_id=project_id
    )
    return ProjectResponse.model_validate(project)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: ProjectService = Depends(get_project_service),
) -> ProjectResponse:
    actor, membership = context
    project = await service.update(
        actor=actor,
        actor_membership=membership,
        project_id=project_id,
        # exclude_unset: absent key = untouched, explicit null = clear
        fields=payload.model_dump(exclude_unset=True),
    )
    return ProjectResponse.model_validate(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: ProjectService = Depends(get_project_service),
) -> None:
    actor, membership = context
    await service.delete(actor=actor, actor_membership=membership, project_id=project_id)


class ProjectMemberAdd(BaseModel):
    user_id: uuid.UUID


class ProjectMemberResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str


def _member_response(user: User) -> ProjectMemberResponse:
    return ProjectMemberResponse(user_id=user.id, email=user.email, full_name=user.full_name)


@router.get("/{project_id}/members", response_model=list[ProjectMemberResponse])
async def list_project_members(
    project_id: uuid.UUID,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: ProjectService = Depends(get_project_service),
) -> list[ProjectMemberResponse]:
    actor, membership = context
    rows = await service.list_project_members(
        actor=actor, actor_membership=membership, project_id=project_id
    )
    return [_member_response(u) for _pm, u in rows]


@router.post(
    "/{project_id}/members",
    response_model=ProjectMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_project_member(
    project_id: uuid.UUID,
    payload: ProjectMemberAdd,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: ProjectService = Depends(get_project_service),
) -> ProjectMemberResponse:
    _actor, membership = context
    _project, target_user = await service.add_project_member(
        actor_membership=membership,
        project_id=project_id,
        target_user_id=payload.user_id,
    )
    return _member_response(target_user)


@router.delete(
    "/{project_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_project_member(
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: ProjectService = Depends(get_project_service),
) -> None:
    _actor, membership = context
    await service.remove_project_member(
        actor_membership=membership, project_id=project_id, target_user_id=user_id
    )
