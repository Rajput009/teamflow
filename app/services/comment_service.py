import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError
from app.models import ActionType, Comment, Membership, OrgRole, Task, User
from app.repositories.activity_repository import ActivityRepository
from app.repositories.comment_repository import CommentRepository
from app.repositories.task_repository import TaskRepository
from app.services.permissions import is_manager_or_above
from app.workers.queue import PostCommitQueue
from app.workers.tasks import create_notification


class CommentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._comments = CommentRepository(session)
        self._tasks = TaskRepository(session)
        self._activities = ActivityRepository(session)
        self._queue = PostCommitQueue(session)

    def _visibility_kwargs(self, actor: User, actor_membership: Membership) -> dict:
        return {
            "org_id": actor_membership.organization_id,
            "user_id": actor.id,
            "sees_all": is_manager_or_above(actor_membership),
        }

    async def _get_visible_task(
        self, actor: User, actor_membership: Membership, task_id: uuid.UUID
    ) -> Task:
        """Single visibility-checked fetch. Callers reuse the returned row
        instead of re-querying."""
        task = await self._tasks.get_in_org(
            task_id,
            actor_membership.organization_id,
            user_id=actor.id,
            sees_all=is_manager_or_above(actor_membership),
        )
        if task is None:
            raise NotFoundError(message="Task not found.")
        return task

    async def list_for_task(
        self,
        *,
        actor: User,
        actor_membership: Membership,
        task_id: uuid.UUID,
        page: int = 1,
        limit: int = 50,
    ) -> tuple[list[tuple[Comment, User]], int]:
        await self._get_visible_task(actor, actor_membership, task_id)
        return await self._comments.list_for_task(
            task_id,
            **self._visibility_kwargs(actor, actor_membership),
            page=page,
            limit=limit,
        )

    async def add(
        self,
        *,
        actor: User,
        actor_membership: Membership,
        task_id: uuid.UUID,
        content: str,
    ) -> Comment:
        # Anyone who can SEE the task can comment on it.
        task = await self._get_visible_task(actor, actor_membership, task_id)
        comment = await self._comments.create(
            task_id=task_id, author_id=actor.id, content=content.strip()
        )

        if (
            task.assigned_to_id is not None
            and task.assigned_to_id != actor.id
        ):
            self._queue.enqueue(
                create_notification,
                str(task.assigned_to_id),
                "COMMENT_ADDED",
                {
                    "task_title": task.title,
                    "actor_name": actor.full_name,
                    "comment_excerpt": content.strip()[:120],
                },
            )
        return comment

    async def delete(
        self, *, actor: User, actor_membership: Membership, comment_id: uuid.UUID
    ) -> None:
        """Author deletes their own comment; ADMIN+ may moderate any."""
        comment = await self._comments.get_visible(
            comment_id, **self._visibility_kwargs(actor, actor_membership)
        )
        if comment is None:
            # foreign-org or nonexistent — indistinguishable on purpose
            raise NotFoundError(message="Comment not found.")

        is_author = comment.author_id == actor.id
        if not (is_author or actor_membership.role in {OrgRole.OWNER, OrgRole.ADMIN}):
            raise ForbiddenError(message="You can only delete your own comments.")
        await self._comments.delete(comment)
        self._activities.record(
            organization_id=actor_membership.organization_id,
            actor_id=actor.id,
            action=ActionType.COMMENT_DELETED,
            entity_type="comment",
            entity_id=comment.id,
            task_id=comment.task_id,
            old_value={"author_id": str(comment.author_id)},
        )
