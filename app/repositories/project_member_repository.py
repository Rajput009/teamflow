import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyProjectMemberError
from app.models import ProjectMember, User


class ProjectMemberRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, project_id: uuid.UUID, user_id: uuid.UUID) -> ProjectMember | None:
        stmt = select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
        return await self._session.scalar(stmt)

    async def is_member(self, project_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        return await self.get(project_id, user_id) is not None

    async def list_members(self, project_id: uuid.UUID) -> list[tuple[ProjectMember, User]]:
        stmt = (
            select(ProjectMember, User)
            .join(User, User.id == ProjectMember.user_id)
            .where(ProjectMember.project_id == project_id)
            .order_by(ProjectMember.created_at, ProjectMember.user_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return [(pm, u) for pm, u in rows]

    async def add(self, *, project_id: uuid.UUID, user_id: uuid.UUID) -> ProjectMember:
        """SAVEPOINT insert: the is_member pre-check in the service is racy
        (two concurrent adds both pass); the loser hits the (project_id,
        user_id) unique constraint and translates to the designed 409. The
        project-creation path also calls this for the creator's membership,
        where a conflict is impossible."""
        member = ProjectMember(project_id=project_id, user_id=user_id)
        self._session.add(member)
        try:
            async with self._session.begin_nested():
                await self._session.flush()
        except IntegrityError:
            raise AlreadyProjectMemberError() from None
        return member

    async def remove(self, member: ProjectMember) -> None:
        await self._session.delete(member)
        await self._session.flush()
