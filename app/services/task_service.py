import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UserNotOrgMemberError,
    ValidationError,
)
from app.core.serialization import json_safe
from app.models import ActionType, Membership, OrgRole, Task, TaskPriority, TaskStatus, User
from app.repositories.activity_repository import ActivityRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.project_repository import ProjectRepository
from app.repositories.task_repository import InvalidOrderingError, TaskRepository
from app.services.permissions import is_manager_or_above, require_role
from app.workers.queue import PostCommitQueue
from app.workers.tasks import create_notification, send_email


class TaskService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._tasks = TaskRepository(session)
        self._projects = ProjectRepository(session)
        self._memberships = MembershipRepository(session)
        self._activities = ActivityRepository(session)
        self._queue = PostCommitQueue(session)

    async def create(
        self,
        *,
        actor: User,
        actor_membership: Membership,
        project_id: uuid.UUID,
        title: str,
        description: str | None,
        status: TaskStatus,
        priority: TaskPriority,
        due_date: date | None,
        assigned_to_id: uuid.UUID | None,
    ) -> Task:
        # V1 note replaced: visibility now follows project membership —
        # MEMBERs can only create tasks in projects they belong to.
        project = await self._projects.get_accessible(
            project_id,
            actor_membership.organization_id,
            user_id=actor.id,
            sees_all=is_manager_or_above(actor_membership),
        )
        if project is None:
            raise NotFoundError(message="Project not found.")

        if assigned_to_id is not None:
            await self._validate_assignee(assigned_to_id, actor_membership.organization_id)

        task = Task(
            project_id=project.id,
            title=title.strip(),
            description=description,
            status=status,
            priority=priority,
            due_date=due_date,
            assigned_to_id=assigned_to_id,
            created_by_id=actor.id,
        )
        self._session.add(task)
        await self._session.flush()

        self._activities.record(
            organization_id=actor_membership.organization_id,
            actor_id=actor.id,
            action=ActionType.TASK_CREATED,
            entity_type="task",
            entity_id=task.id,
            project_id=project.id,
            task_id=task.id,
            new_value={"title": task.title, "priority": task.priority.value},
        )
        if assigned_to_id is not None:
            # assignment at creation time must be audited too — the log's
            # promise ("who assigned what, when") has no exceptions.
            self._activities.record(
                organization_id=actor_membership.organization_id,
                actor_id=actor.id,
                action=ActionType.TASK_ASSIGNED,
                entity_type="task",
                entity_id=task.id,
                project_id=project.id,
                task_id=task.id,
                new_value={"assigned_to": str(assigned_to_id)},
            )
            if assigned_to_id != actor.id:
                self._notify_assignment(
                    recipient_id=assigned_to_id,
                    actor_name=actor.full_name,
                    task_title=task.title,
                    task_id=task.id,
                )
        return task

    async def get_in_org(
        self, *, actor: User, actor_membership: Membership, task_id: uuid.UUID
    ) -> Task:
        task = await self._tasks.get_in_org(
            task_id,
            actor_membership.organization_id,
            user_id=actor.id,
            sees_all=is_manager_or_above(actor_membership),
        )
        if task is None:
            raise NotFoundError(message="Task not found.")
        return task

    async def list_for_project(
        self,
        *,
        actor: User,
        actor_membership: Membership,
        project_id: uuid.UUID,
        status: TaskStatus | None = None,
        priority: TaskPriority | None = None,
        assigned_to: uuid.UUID | None = None,
        search: str | None = None,
        ordering: str = "-created_at",
        page: int = 1,
        limit: int = 20,
    ) -> tuple[list[Task], int]:
        project = await self._projects.get_accessible(
            project_id,
            actor_membership.organization_id,
            user_id=actor.id,
            sees_all=is_manager_or_above(actor_membership),
        )
        if project is None:
            raise NotFoundError(message="Project not found.")
        try:
            return await self._tasks.list_for_project(
                project_id=project_id,
                status=status,
                priority=priority,
                assigned_to=assigned_to,
                search=search,
                ordering=ordering,
                page=page,
                limit=limit,
            )
        except InvalidOrderingError as exc:
            raise ValidationError(
                message=f"Invalid ordering value: '{exc.args[0]}'."
            ) from None

    async def update(
        self,
        *,
        actor: User,
        actor_membership: Membership,
        task_id: uuid.UUID,
        fields: dict[str, object],
    ) -> Task:
        """PATCH semantics via an exclude_unset dump: a key ABSENT from
        `fields` means untouched; an explicit None means "clear" (legal only
        for nullable columns — the schemas reject the rest)."""
        task = await self.get_in_org(
            actor=actor, actor_membership=actor_membership, task_id=task_id
        )

        # Matrix row 'Update task fields': MANAGER+ freely; MEMBER only tasks
        # assigned to them.
        if not is_manager_or_above(actor_membership) and task.assigned_to_id != actor.id:
            raise ForbiddenError()

        changes: dict[str, dict] = {}
        if "title" in fields:
            clean = str(fields["title"]).strip()
            if task.title != clean:
                changes["title"] = {"old": task.title, "new": clean}
                task.title = clean
        if "description" in fields:
            value = fields["description"]
            if task.description != value:
                changes["description"] = {"old": task.description, "new": value}
                task.description = value
        if "status" in fields and task.status != fields["status"]:
            changes["status"] = {"old": task.status.value, "new": fields["status"].value}
            task.status = fields["status"]
        if "priority" in fields and task.priority != fields["priority"]:
            changes["priority"] = {
                "old": task.priority.value,
                "new": fields["priority"].value,
            }
            task.priority = fields["priority"]
        if "due_date" in fields and task.due_date != fields["due_date"]:
            # dates aren't JSON-serializable — snapshot them as ISO strings
            changes["due_date"] = {
                "old": json_safe(task.due_date),
                "new": json_safe(fields["due_date"]),
            }
            task.due_date = fields["due_date"]

        await self._flush_or_conflict()

        if changes:
            action = (
                ActionType.TASK_STATUS_CHANGED
                if set(changes) == {"status"}
                else ActionType.TASK_UPDATED
            )
            self._activities.record(
                organization_id=actor_membership.organization_id,
                actor_id=actor.id,
                action=action,
                entity_type="task",
                entity_id=task.id,
                project_id=task.project_id,
                task_id=task.id,
                old_value={k: v["old"] for k, v in changes.items()},
                new_value={k: v["new"] for k, v in changes.items()},
            )
        return task

    async def delete(
        self, *, actor: User, actor_membership: Membership, task_id: uuid.UUID
    ) -> None:
        require_role(
            actor_membership, {OrgRole.OWNER, OrgRole.ADMIN, OrgRole.MANAGER}
        )
        task = await self.get_in_org(
            actor=actor, actor_membership=actor_membership, task_id=task_id
        )
        await self._session.delete(task)
        await self._session.flush()

        # Deletion is a mutation like any other — the log records that the
        # task EXISTED and who removed it. task_id must stay NULL here:
        # activities.task_id cascades on task delete, so referencing the
        # doomed row is both unwritable now and self-erasing later. The
        # deleted id lives in entity_id, which carries no FK.
        self._activities.record(
            organization_id=actor_membership.organization_id,
            actor_id=actor.id,
            action=ActionType.TASK_DELETED,
            entity_type="task",
            entity_id=task.id,
            project_id=task.project_id,
            task_id=None,
            old_value={"title": task.title},
        )

    async def assign(
        self,
        *,
        actor: User,
        actor_membership: Membership,
        task_id: uuid.UUID,
        target_user_id: uuid.UUID,
    ) -> Task:
        require_role(
            actor_membership, {OrgRole.OWNER, OrgRole.ADMIN, OrgRole.MANAGER}
        )
        task = await self.get_in_org(
            actor=actor, actor_membership=actor_membership, task_id=task_id
        )
        await self._validate_assignee(target_user_id, actor_membership.organization_id)
        if task.assigned_to_id == target_user_id:
            # No-op assignment: no flush, no audit row, no duplicate
            # notification — matching the no-op PATCH rule elsewhere.
            return task
        previous_assignee = task.assigned_to_id
        task.assigned_to_id = target_user_id
        await self._flush_or_conflict()

        self._activities.record(
            organization_id=actor_membership.organization_id,
            actor_id=actor.id,
            action=ActionType.TASK_ASSIGNED,
            entity_type="task",
            entity_id=task.id,
            project_id=task.project_id,
            task_id=task.id,
            old_value={"assigned_to": str(previous_assignee)} if previous_assignee else None,
            new_value={"assigned_to": str(target_user_id)},
        )
        if target_user_id != actor.id:
            self._notify_assignment(
                recipient_id=target_user_id,
                actor_name=actor.full_name,
                task_title=task.title,
                task_id=task.id,
            )
        return task

    def _notify_assignment(
        self,
        *,
        recipient_id: uuid.UUID,
        actor_name: str,
        task_title: str,
        task_id: uuid.UUID,
    ) -> None:
        payload = {"task_title": task_title, "actor_name": actor_name}
        # event-scoped key: the same assignment delivered twice yields one inbox
        # entry (Celery retries / repeated events cannot duplicate it)
        idempotency_key = f"assign:{task_id}:{recipient_id}"
        self._queue.enqueue(
            create_notification,
            str(recipient_id),
            "TASK_ASSIGNED",
            payload,
            idempotency_key,
        )
        # email side-effect rides the same commit; the worker resolves the
        # recipient's real address from their user id (console transport in dev)
        self._queue.enqueue(
            send_email,
            str(recipient_id),
            "You were assigned a new task",
            f"{actor_name} assigned you: {task_title}",
        )

    async def _validate_assignee(
        self, user_id: uuid.UUID, org_id: uuid.UUID
    ) -> None:
        """Cross-entity rule the DB FK cannot express: assignees must belong
        to the task's organization."""
        membership = await self._memberships.get_for_user(org_id, user_id)
        if membership is None:
            raise UserNotOrgMemberError()

    async def _flush_or_conflict(self) -> None:
        """Flush with optimistic-locking translation.

        The version column makes every UPDATE carry WHERE version = N; if
        another writer committed first, zero rows match and SQLAlchemy raises
        StaleDataError. Without this translation the client would see an
        opaque 500 — with it, an actionable 409 telling them to reload.
        """
        try:
            await self._session.flush()
        except StaleDataError:
            raise ConflictError(
                message=(
                    "This task was modified by someone else. "
                    "Reload it and reapply your change."
                )
            ) from None
