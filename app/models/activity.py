import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Activity(Base):
    """Append-only audit trail. Rows are INSERT-only by convention:
    no service ever updates or deletes them.

    No TimestampMixin — updated_at is meaningless for immutable rows.

    Known cascade semantics: task_id/project_id are ON DELETE CASCADE, so
    deleting a task erases ITS history rows (the org-level trail keeps only
    the TASK_DELETED marker, which deliberately leaves task_id NULL). If
    full post-deletion history ever becomes a requirement, migrate these
    columns to ON DELETE SET NULL.
    """

    __tablename__ = "activities"
    __table_args__ = (
        Index("ix_activities_org_created", "organization_id", "created_at"),
        Index("ix_activities_task_created", "task_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=True,
    )
    # No ondelete: the audit trail must outlive its actors. A future user
    # deletion flow should anonymize actor identity, never cascade-delete
    # history — see the FK-policy note in task.py.
    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    old_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), nullable=False
    )


class ActionType(enum.StrEnum):
    """Canonical action names. Free-form strings in the DB (plain String
    column — new values never need a migration), but defined here so writers
    and filters share one vocabulary."""

    PROJECT_CREATED = "project.created"
    PROJECT_UPDATED = "project.updated"

    TASK_CREATED = "task.created"
    TASK_UPDATED = "task.updated"
    TASK_STATUS_CHANGED = "task.status_changed"
    TASK_ASSIGNED = "task.assigned"
    TASK_DELETED = "task.deleted"

    COMMENT_DELETED = "comment.deleted"

    MEMBER_ROLE_UPDATED = "member.role_updated"
    MEMBER_REMOVED = "member.removed"

    PROJECT_MEMBER_ADDED = "project_member.added"
    PROJECT_MEMBER_REMOVED = "project_member.removed"

    # AI layer — every accepted proposal is audited with its human approver
    AI_PROJECT_CREATED = "ai.project_created"
    AI_TASKS_GENERATED = "ai.tasks_generated"
