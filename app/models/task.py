import enum
import uuid
from datetime import date

from sqlalchemy import Date, Enum, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class TaskStatus(enum.StrEnum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    IN_REVIEW = "IN_REVIEW"
    COMPLETED = "COMPLETED"


class TaskPriority(enum.StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


class Task(TimestampMixin, Base):
    """Tasks reach their organization through the parent project — the JOIN
    in TaskRepository is what makes tenancy scoping work for tasks."""

    __tablename__ = "tasks"
    __table_args__ = (
        # backs the most common filter: tasks of a project by status (board view)
        Index("ix_tasks_project_id_status", "project_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Optimistic-locking counter: every UPDATE carries WHERE version = N, so
    # a writer who lost a race gets StaleDataError (translated to 409)
    # instead of silently overwriting a colleague's changes.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    __mapper_args__ = {"version_id_col": version}

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="task_status", native_enum=True),
        nullable=False,
        default=TaskStatus.TODO,
    )
    priority: Mapped[TaskPriority] = mapped_column(
        Enum(TaskPriority, name="task_priority", native_enum=True),
        nullable=False,
        default=TaskPriority.MEDIUM,
    )
    # FK POLICY — user-facing columns have NO ondelete on purpose. There is
    # no DELETE /users endpoint yet; the day one lands, it must first
    # reassign tasks, transfer org ownership, and (for audit integrity)
    # anonymize rather than cascade-delete activity actors. The bare FKs make
    # premature deletion fail loudly instead of silently destroying history.
    assigned_to_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True
    )
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
