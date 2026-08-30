import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Comment, Project, ProjectMember, Task, User


class CommentRepository:
    """Tenancy + visibility chain: comment → task → project → organization,
    with MEMBER visibility narrowing via project_members."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _visible_stmt(self):
        return (
            select(Comment)
            .join(Task, Task.id == Comment.task_id)
            .join(Project, Project.id == Task.project_id)
        )

    def _visibility_filter(
        self,
        *,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        sees_all: bool,
    ):
        conditions = [Project.organization_id == org_id]
        if not sees_all:
            conditions.append(
                Comment.task_id.in_(
                    select(Task.id)
                    .join(ProjectMember, ProjectMember.project_id == Task.project_id)
                    .where(ProjectMember.user_id == user_id)
                )
            )
        return *conditions,

    async def get_visible(
        self,
        comment_id: uuid.UUID,
        *,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        sees_all: bool,
    ) -> Comment | None:
        stmt = self._visible_stmt().where(
            Comment.id == comment_id,
            *self._visibility_filter(org_id=org_id, user_id=user_id, sees_all=sees_all),
        )
        return await self._session.scalar(stmt)

    async def list_for_task(
        self,
        task_id: uuid.UUID,
        *,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        sees_all: bool,
        page: int = 1,
        limit: int = 50,
    ) -> tuple[list[tuple[Comment, User]], int]:
        from sqlalchemy import func

        visibility = self._visibility_filter(
            org_id=org_id, user_id=user_id, sees_all=sees_all
        )
        total = await self._session.scalar(
            select(func.count())
            .select_from(Comment)
            .join(Task, Task.id == Comment.task_id)
            .join(Project, Project.id == Task.project_id)
            .where(Comment.task_id == task_id, *visibility)
        )
        stmt = (
            select(Comment, User)  # explicit pair — a plain select(Comment) would
            # collapse each row to just the comment entity
            .join(Task, Task.id == Comment.task_id)
            .join(Project, Project.id == Task.project_id)
            .join(User, User.id == Comment.author_id)
            .where(
                Comment.task_id == task_id,
                *visibility,
            )
            .order_by(Comment.created_at.asc(), Comment.id.asc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        return [(c, u) for c, u in rows], int(total or 0)

    async def list_for_project(
        self,
        *,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        sees_all: bool,
        page: int = 1,
        limit: int = 20,
    ) -> tuple[list[tuple[Comment, User]], int]:
        visibility = self._visibility_filter(
            org_id=org_id, user_id=user_id, sees_all=sees_all
        )
        total = await self._session.scalar(
            select(func.count())
            .select_from(Comment)
            .join(Task, Task.id == Comment.task_id)
            .join(Project, Project.id == Task.project_id)
            .where(Task.project_id == project_id, *visibility)
        )
        stmt = (
            select(Comment, User)
            .join(Task, Task.id == Comment.task_id)
            .join(Project, Project.id == Task.project_id)
            .join(User, User.id == Comment.author_id)
            .where(Task.project_id == project_id, *visibility)
            .order_by(Comment.created_at.desc(), Comment.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        return [(c, u) for c, u in rows], int(total or 0)

    async def create(self, *, task_id: uuid.UUID, author_id: uuid.UUID, content: str) -> Comment:
        comment = Comment(task_id=task_id, author_id=author_id, content=content.strip())
        self._session.add(comment)
        await self._session.flush()
        return comment

    async def delete(self, comment: Comment) -> None:
        await self._session.delete(comment)
        await self._session.flush()
