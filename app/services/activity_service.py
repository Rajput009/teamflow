import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models import Activity, Membership, User
from app.repositories.activity_repository import ActivityRepository
from app.services.permissions import is_manager_or_above


class ActivityService:
    """Read-side of the audit trail. Writing happens inside the services
    whose changes are being recorded (via ActivityRepository.record)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._activities = ActivityRepository(session)

    async def list_org_activities(
        self,
        *,
        actor_membership: Membership,
        action: str | None,
        actor_id: uuid.UUID | None,
        page: int,
        limit: int,
    ) -> tuple[list[tuple[Activity, User]], int]:
        # V2 simplification (documented): org members see the full org log.
        return await self._activities.list_for_org(
            actor_membership.organization_id,
            action=action,
            actor_id=actor_id,
            page=page,
            limit=limit,
        )

    async def list_project_activities(
        self,
        *,
        actor: User,
        actor_membership: Membership,
        project_id: uuid.UUID,
        page: int,
        limit: int,
    ) -> tuple[list[tuple[Activity, User]], int]:
        exists = await self._activities.visible_project_exists(
            project_id,
            org_id=actor_membership.organization_id,
            user_id=actor.id,
            sees_all=is_manager_or_above(actor_membership),
        )
        if not exists:
            raise NotFoundError(message="Project not found.")
        return await self._activities.list_for_project(project_id, page=page, limit=limit)
