import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ProjectNameExistsError
from app.models import Project, ProjectMember


class ProjectRepository:
    """Every method takes organization_id — the golden rule from
    05-rbac-multi-tenancy.md. There is NO unscoped get_by_id here to misuse."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_in_org(self, project_id: uuid.UUID, org_id: uuid.UUID) -> Project | None:
        stmt = select(Project).where(
            Project.id == project_id, Project.organization_id == org_id
        )
        return await self._session.scalar(stmt)

    async def get_accessible(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        *,
        user_id: uuid.UUID,
        sees_all: bool,
    ) -> Project | None:
        """Single entry point for scoped reads. MANAGER+ see all org projects;
        MEMBERs only projects listed in project_members."""
        if sees_all:
            return await self.get_in_org(project_id, org_id)
        return await self.get_visible_for_member(project_id, org_id, user_id)

    async def list_accessible(
        self,
        org_id: uuid.UUID,
        *,
        user_id: uuid.UUID,
        sees_all: bool,
        page: int = 1,
        limit: int = 20,
    ) -> tuple[list[Project], int]:
        if sees_all:
            return await self.list_for_org(org_id, page=page, limit=limit)
        return await self.list_visible_for_member(
            org_id, user_id, page=page, limit=limit
        )

    async def get_visible_for_member(
        self, project_id: uuid.UUID, org_id: uuid.UUID, user_id: uuid.UUID
    ) -> Project | None:
        """MEMBER-scoped read: the id must also appear in project_members."""
        stmt = (
            select(Project)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(
                Project.id == project_id,
                Project.organization_id == org_id,
                ProjectMember.user_id == user_id,
            )
        )
        return await self._session.scalar(stmt)

    async def list_for_org(
        self, org_id: uuid.UUID, *, page: int = 1, limit: int = 20
    ) -> tuple[list[Project], int]:
        base = select(Project).where(Project.organization_id == org_id)

        total = await self._session.scalar(
            select(func.count()).select_from(Project).where(
                Project.organization_id == org_id
            )
        )
        stmt = base.order_by(Project.created_at.desc(), Project.id.desc()).offset(
            (page - 1) * limit
        ).limit(limit)
        items = list((await self._session.scalars(stmt)).all())
        return items, int(total or 0)

    async def list_visible_for_member(
        self, org_id: uuid.UUID, user_id: uuid.UUID, *, page: int = 1, limit: int = 20
    ) -> tuple[list[Project], int]:
        visibility = (
            select(Project.id)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(
                Project.organization_id == org_id,
                ProjectMember.user_id == user_id,
            )
            .scalar_subquery()
        )

        total = await self._session.scalar(
            select(func.count())
            .select_from(Project)
            .where(Project.id.in_(visibility))
        )
        stmt = (
            select(Project)
            .where(Project.id.in_(visibility))
            .order_by(Project.created_at.desc(), Project.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        items = list((await self._session.scalars(stmt)).all())
        return items, int(total or 0)

    async def name_exists_in_org(self, org_id: uuid.UUID, name: str) -> bool:
        stmt = select(func.count()).select_from(Project).where(
            Project.organization_id == org_id,
            func.lower(Project.name) == name.lower(),
        )
        return bool(await self._session.scalar(stmt))

    async def create(
        self,
        *,
        org_id: uuid.UUID,
        created_by_id: uuid.UUID,
        name: str,
        description: str | None,
        deadline: date | None,
    ) -> Project:
        """SAVEPOINT insert: two managers creating the same-name project
        concurrently both pass the service's name_exists pre-check; the
        loser hits uq_projects_org_lower_name. The savepoint keeps the outer
        transaction (creator's project_members row, activity row) alive and
        we translate to the designed 409."""
        project = Project(
            organization_id=org_id,
            created_by_id=created_by_id,
            name=name,
            description=description,
            deadline=deadline,
        )
        self._session.add(project)
        try:
            async with self._session.begin_nested():
                await self._session.flush()
        except IntegrityError:
            raise ProjectNameExistsError() from None
        return project

    async def delete(self, project: Project) -> None:
        await self._session.delete(project)
        await self._session.flush()
