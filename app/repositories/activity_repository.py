import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Activity, Project, ProjectMember, User


class ActivityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def record(
        self,
        *,
        organization_id: uuid.UUID,
        actor_id: uuid.UUID,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID,
        project_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        old_value: dict | None = None,
        new_value: dict | None = None,
    ) -> Activity:
        """Append one activity row to the CURRENT session/transaction.

        Because this uses the request's own session (just add + flush), the
        row commits or rolls back together with the state change it describes.
        That coupling is the whole point of an audit trail — do not "optimize"
        this into a separate session or background job.
        """
        activity = Activity(
            organization_id=organization_id,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            project_id=project_id,
            task_id=task_id,
            old_value=old_value,
            new_value=new_value,
        )
        self._session.add(activity)
        # no flush needed here; caller's next flush/commit carries it
        return activity

    async def list_for_org(
        self,
        org_id: uuid.UUID,
        *,
        action: str | None = None,
        actor_id: uuid.UUID | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> tuple[list[tuple[Activity, User]], int]:
        conditions = [Activity.organization_id == org_id]
        if action is not None:
            conditions.append(Activity.action == action)
        if actor_id is not None:
            conditions.append(Activity.actor_id == actor_id)

        total = await self._session.scalar(
            select(func.count())
            .select_from(Activity)
            .where(*conditions)
        )
        stmt = (
            select(Activity, User)
            .join(User, User.id == Activity.actor_id)
            .where(*conditions)
            .order_by(Activity.created_at.desc(), Activity.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        return [(a, u) for a, u in rows], int(total or 0)

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        page: int = 1,
        limit: int = 20,
    ) -> tuple[list[tuple[Activity, User]], int]:
        base_conditions = [Activity.project_id == project_id]
        total = await self._session.scalar(
            select(func.count()).select_from(Activity).where(*base_conditions)
        )
        stmt = (
            select(Activity, User)
            .join(User, User.id == Activity.actor_id)
            .where(*base_conditions)
            .order_by(Activity.created_at.desc(), Activity.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        return [(a, u) for a, u in rows], int(total or 0)

    async def visible_project_exists(
        self,
        project_id: uuid.UUID,
        *,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        sees_all: bool,
    ) -> bool:
        conditions = [
            Project.id == project_id,
            Project.organization_id == org_id,
        ]
        if not sees_all:
            conditions.append(
                Project.id.in_(
                    select(ProjectMember.project_id).where(
                        ProjectMember.user_id == user_id
                    )
                )
            )
        stmt = select(Project.id).where(*conditions)
        return await self._session.scalar(stmt) is not None
