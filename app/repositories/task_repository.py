import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import ColumnElement, and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectMember, Task, TaskPriority, TaskStatus, User


class InvalidOrderingError(Exception):
    pass


# Whitelist: client input NEVER becomes a raw column name.
_ORDERING_MAP: dict[str, ColumnElement] = {
    "created_at": Task.created_at.asc(),
    "-created_at": Task.created_at.desc(),
    "due_date": Task.due_date.asc(),
    "-due_date": Task.due_date.desc(),
    "priority": Task.priority.desc(),  # enum stored as text; desc() puts URGENT first
    "-priority": Task.priority.asc(),
}


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_in_org(
        self,
        task_id: uuid.UUID,
        org_id: uuid.UUID,
        *,
        user_id: uuid.UUID | None = None,
        sees_all: bool = True,
    ) -> Task | None:
        """Scoped fetch via JOIN to the parent project's tenancy anchor.
        When the caller is a MEMBER (sees_all=False), the parent project must
        also appear in project_members for that user."""
        stmt = (
            select(Task)
            .join(Project, Project.id == Task.project_id)
            .where(Task.id == task_id, Project.organization_id == org_id)
        )
        if not sees_all and user_id is not None:
            stmt = stmt.where(
                exists(
                    select(ProjectMember.id).where(
                        and_(
                            ProjectMember.project_id == Project.id,
                            ProjectMember.user_id == user_id,
                        )
                    )
                )
            )
        return await self._session.scalar(stmt)

    async def list_for_project(
        self,
        *,
        project_id: uuid.UUID,
        status: TaskStatus | None = None,
        priority: TaskPriority | None = None,
        assigned_to: uuid.UUID | None = None,
        search: str | None = None,
        ordering: str = "-created_at",
        page: int = 1,
        limit: int = 20,
    ) -> tuple[list[Task], int]:
        conditions = [Task.project_id == project_id]
        if status is not None:
            conditions.append(Task.status == status)
        if priority is not None:
            conditions.append(Task.priority == priority)
        if assigned_to is not None:
            conditions.append(Task.assigned_to_id == assigned_to)
        if search:
            # Escape LIKE wildcards: a user searching "50%" means the literal
            # string, and raw "%"/"_" would match everything (slow-query
            # nuisance at scale). Not an injection risk — parameterized —
            # just wrong results.
            escaped = (
                search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            conditions.append(Task.title.ilike(f"%{escaped}%", escape="\\"))

        total = await self._session.scalar(
            select(func.count()).select_from(Task).where(*conditions)
        )

        order_clause = _ORDERING_MAP.get(ordering)
        if order_clause is None:
            raise InvalidOrderingError(ordering)

        stmt = (
            select(Task)
            .where(*conditions)
            # Task.id tiebreaker: clock_timestamp() makes collisions rare but
            # concurrent transactions can still interleave — without the
            # tiebreaker the order within identical timestamps is
            # nondeterministic and page boundaries can repeat/skip rows.
            .order_by(order_clause, Task.created_at.desc(), Task.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        items = list((await self._session.scalars(stmt)).all())
        return items, int(total or 0)

    # --- summary aggregates (AI V2) ---
    # Ground truth for the summarizer: every number the LLM narrates comes
    # from one of these queries — the model interprets facts, never invents
    # them.

    async def status_counts(self, project_id: uuid.UUID) -> dict[str, int]:
        stmt = (
            select(Task.status, func.count())
            .where(Task.project_id == project_id)
            .group_by(Task.status)
        )
        return {
            status.value: count
            for status, count in (await self._session.execute(stmt)).all()
        }

    def _open(self, project_id: uuid.UUID) -> ColumnElement[bool]:
        return and_(
            Task.project_id == project_id,
            Task.status != TaskStatus.COMPLETED,
        )

    async def overdue_count(self, project_id: uuid.UUID, today: date) -> int:
        stmt = select(func.count()).select_from(Task).where(
            self._open(project_id), Task.due_date < today
        )
        return int(await self._session.scalar(stmt) or 0)

    async def unassigned_high_urgent_count(self, project_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(Task).where(
            self._open(project_id),
            Task.assigned_to_id.is_(None),
            Task.priority.in_([TaskPriority.HIGH, TaskPriority.URGENT]),
        )
        return int(await self._session.scalar(stmt) or 0)

    async def due_within_week_count(
        self, project_id: uuid.UUID, today: date
    ) -> int:
        stmt = select(func.count()).select_from(Task).where(
            self._open(project_id),
            Task.due_date >= today,
            Task.due_date <= today + timedelta(days=7),
        )
        return int(await self._session.scalar(stmt) or 0)

    async def stale_open_count(
        self, project_id: uuid.UUID, cutoff: datetime
    ) -> int:
        stmt = select(func.count()).select_from(Task).where(
            self._open(project_id), Task.updated_at < cutoff
        )
        return int(await self._session.scalar(stmt) or 0)

    async def open_workload_top(
        self, project_id: uuid.UUID, limit: int = 5
    ) -> list[dict]:
        """Open tasks per assignee — the workload signal for prioritization."""
        stmt = (
            select(User.email, func.count().label("open_tasks"))
            .join(Task, Task.assigned_to_id == User.id)
            .where(self._open(project_id))
            .group_by(User.email)
            .order_by(func.count().desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        return [{"email": email, "open_tasks": count} for email, count in rows]
