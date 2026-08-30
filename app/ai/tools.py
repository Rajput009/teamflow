"""AI V5 agent tool registry.

The agent NEVER executes arbitrary code. It can only call the small, audited set
of tools defined here. Each tool:

  * is an async function that mutates state ONLY through existing, permission-
    enforcing services (TaskService, CommentService, ...);
  * receives the *request* project_id (the path project) rather than trusting a
    project_id from the model, so the model cannot redirect writes elsewhere;
  * validates its arguments with a pydantic model before running.

The registry is intentionally a literal dict, not discovered dynamically, so a
code review can see the entire blast radius at a glance.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError

from app.models import TaskPriority, TaskStatus
from app.services.permissions import is_manager_or_above


class CreateTaskArgs(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    priority: TaskPriority = TaskPriority.MEDIUM
    due_in_days: int | None = Field(default=None, ge=0, le=365)


class AddCommentArgs(BaseModel):
    task_id: UUID
    content: str = Field(min_length=1, max_length=2000)


class AssignTaskArgs(BaseModel):
    task_id: UUID
    assignee_email: str = Field(min_length=3, max_length=320)


class UpdateTaskStatusArgs(BaseModel):
    task_id: UUID
    status: TaskStatus


async def _run_create_task(
    session: Any,
    actor: Any,
    membership: Any,
    project_id: UUID,
    args: CreateTaskArgs,
) -> dict:
    from app.services.task_service import TaskService

    due: date | None = None
    if args.due_in_days is not None:
        # server-clock relative; the agent must not pick absolute dates
        due = datetime.now(UTC).date() + timedelta(days=args.due_in_days)

    task = await TaskService(session).create(
        actor=actor,
        actor_membership=membership,
        project_id=project_id,  # path project, never from args
        title=args.title,
        description=args.description,
        status=TaskStatus.TODO,
        priority=args.priority,
        due_date=due,
        assigned_to_id=None,
    )
    return {"task_id": str(task.id), "title": task.title}


async def _run_add_comment(
    session: Any,
    actor: Any,
    membership: Any,
    project_id: UUID,
    args: AddCommentArgs,
) -> dict:
    from app.services.comment_service import CommentService

    # the task must belong to the project the agent is operating on
    await _require_task_in_project(session, actor, membership, project_id, args.task_id)

    comment = await CommentService(session).add(
        actor=actor,
        actor_membership=membership,
        task_id=args.task_id,
        content=args.content,
    )
    return {"comment_id": str(comment.id)}


async def _require_task_in_project(
    session: Any, actor: Any, membership: Any, project_id: UUID, task_id: UUID
) -> None:
    from app.repositories.task_repository import TaskRepository

    task = await TaskRepository(session).get_in_org(
        task_id,
        membership.organization_id,
        user_id=actor.id,
        sees_all=is_manager_or_above(membership),
    )
    if task is None or task.project_id != project_id:
        raise ValueError("task does not belong to this project")


async def _run_assign_task(
    session: Any,
    actor: Any,
    membership: Any,
    project_id: UUID,
    args: AssignTaskArgs,
) -> dict:
    from app.repositories.membership_repository import MembershipRepository
    from app.repositories.user_repository import UserRepository
    from app.services.task_service import TaskService

    await _require_task_in_project(session, actor, membership, project_id, args.task_id)

    email = args.assignee_email.strip().lower()
    user = await UserRepository(session).get_by_email(email)
    if user is None:
        raise ValueError("no registered user with that email")
    target = await MembershipRepository(session).get_for_user(
        membership.organization_id, user.id
    )
    if target is None:
        raise ValueError("assignee is not a member of this organization")

    task = await TaskService(session).assign(
        actor=actor,
        actor_membership=membership,
        task_id=args.task_id,
        target_user_id=user.id,
    )
    return {"task_id": str(task.id), "assigned_to_id": str(user.id)}


async def _run_update_task_status(
    session: Any,
    actor: Any,
    membership: Any,
    project_id: UUID,
    args: UpdateTaskStatusArgs,
) -> dict:
    from app.services.task_service import TaskService

    await _require_task_in_project(session, actor, membership, project_id, args.task_id)

    task = await TaskService(session).update(
        actor=actor,
        actor_membership=membership,
        task_id=args.task_id,
        fields={"status": args.status},
    )
    return {"task_id": str(task.id), "status": task.status.value}


# name -> (args schema, runner). Visible, auditable, not auto-discovered.
AGENT_TOOLS: dict[str, dict[str, Any]] = {
    "create_task": {"args": CreateTaskArgs, "run": _run_create_task},
    "add_comment": {"args": AddCommentArgs, "run": _run_add_comment},
    "assign_task": {"args": AssignTaskArgs, "run": _run_assign_task},
    "update_task_status": {"args": UpdateTaskStatusArgs, "run": _run_update_task_status},
}


def describe_tools() -> dict[str, dict[str, Any]]:
    """JSON-schema description of allowed tools for the model prompt."""
    return {
        name: {"args": spec["args"].model_json_schema()}
        for name, spec in AGENT_TOOLS.items()
    }


def validate_call(tool: str, args: dict) -> BaseModel:
    """Validate a single proposed call. Raises ValueError if tool unknown or
    args invalid. Used by the service before execution (and as a safety net)."""
    spec = AGENT_TOOLS.get(tool)
    if spec is None:
        raise ValueError(f"unknown tool: {tool}")
    try:
        return spec["args"].model_validate(args)
    except ValidationError as exc:  # pydantic v2: not a ValueError subclass
        raise ValueError(f"invalid args for {tool}: {exc}") from None


async def run_tool(
    tool: str,
    session: Any,
    actor: Any,
    membership: Any,
    project_id: UUID,
    args: dict,
) -> dict:
    """Execute a validated tool call through its service. Caller must have
    already validated `args` via `validate_call`."""
    spec = AGENT_TOOLS[tool]
    args_model = spec["args"].model_validate(args)
    runner: Callable[..., Awaitable[dict]] = spec["run"]
    return await runner(session, actor, membership, project_id, args_model)
