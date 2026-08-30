import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_membership, get_current_user, get_db
from app.core.exceptions import ForbiddenError
from app.models import Membership, OrgRole, User
from app.schemas.common import Page
from app.schemas.organization import (
    MemberAdd,
    MemberResponse,
    MemberRoleUpdate,
    MyMembershipResponse,
    OrganizationCreate,
    OrganizationResponse,
    OrganizationUpdate,
)
from app.services.organization_service import OrganizationService

router = APIRouter(prefix="/organizations", tags=["organizations"])


def get_org_service(session: AsyncSession = Depends(get_db)) -> OrganizationService:
    return OrganizationService(session)


@router.post("", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED)
async def create_organization(
    payload: OrganizationCreate,
    current_user: User = Depends(get_current_user),
    service: OrganizationService = Depends(get_org_service),
) -> OrganizationResponse:
    """Create an organization. Creator becomes its OWNER atomically."""
    org = await service.create(
        actor=current_user, name=payload.name, description=payload.description
    )
    return OrganizationResponse.model_validate(org)


@router.get("/current", response_model=MyMembershipResponse)
async def get_current_organization(
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: OrganizationService = Depends(get_org_service),
) -> MyMembershipResponse:
    user, membership = context
    org, _ = await service.get_for_actor(user)
    return MyMembershipResponse(
        organization=OrganizationResponse.model_validate(org),
        my_role=membership.role,
    )


@router.patch("/current", response_model=OrganizationResponse)
async def update_current_organization(
    payload: OrganizationUpdate,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: OrganizationService = Depends(get_org_service),
) -> OrganizationResponse:
    _, membership = context
    org = await service.update(
        actor_membership=membership,
        # exclude_unset: absent key = untouched, explicit null = clear
        fields=payload.model_dump(exclude_unset=True),
    )
    return OrganizationResponse.model_validate(org)


@router.delete("/current", status_code=status.HTTP_204_NO_CONTENT)
async def delete_current_organization(
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: OrganizationService = Depends(get_org_service),
) -> None:
    _, membership = context
    await service.delete(actor_membership=membership)


@router.get("/members", response_model=Page[MemberResponse])
async def list_members(
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: OrganizationService = Depends(get_org_service),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> Page[MemberResponse]:
    actor, _membership = context
    rows, total = await service.list_members(actor=actor, page=page, limit=limit)
    items = [
        MemberResponse(
            user_id=m.user_id, email=u.email, full_name=u.full_name, role=m.role
        )
        for m, u in rows
    ]
    return Page[MemberResponse].build(items=items, total=total, page=page, limit=limit)


@router.post("/members", response_model=MemberResponse, status_code=status.HTTP_201_CREATED)
async def add_member(
    payload: MemberAdd,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: OrganizationService = Depends(get_org_service),
) -> MemberResponse:
    """Add an existing registered user to the organization (ADMIN+)."""
    _, membership = context
    target_membership, target_user = await service.add_member(
        actor_membership=membership, email=payload.email, role=payload.role
    )
    return MemberResponse(
        user_id=target_user.id,
        email=target_user.email,
        full_name=target_user.full_name,
        role=target_membership.role,
    )


@router.patch("/members/{user_id}", response_model=MemberResponse)
async def change_member_role(
    user_id: uuid.UUID,
    payload: MemberRoleUpdate,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: OrganizationService = Depends(get_org_service),
) -> MemberResponse:
    _, membership = context
    if membership.user_id == user_id and membership.role != OrgRole.OWNER:
        raise ForbiddenError(message="You cannot change your own role.")
    target = await service.change_role(
        actor_membership=membership, target_user_id=user_id, new_role=payload.role
    )
    target_user = await service.get_user(target.user_id)
    return MemberResponse(
        user_id=target.user_id,
        email=target_user.email,
        full_name=target_user.full_name,
        role=target.role,
    )


@router.delete("/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    user_id: uuid.UUID,
    context: tuple[User, Membership] = Depends(get_current_membership),
    service: OrganizationService = Depends(get_org_service),
) -> None:
    _, membership = context
    await service.remove_member(
        actor_membership=membership, target_user_id=user_id
    )
