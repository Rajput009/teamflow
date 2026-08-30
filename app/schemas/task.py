import uuid
from datetime import UTC, date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import TaskPriority, TaskStatus
from app.schemas.validators import NotBlankStr, SafeStr


def _validate_deadline_not_past(value: date | None) -> date | None:
    # UTC, not date.today(): the server's LOCAL midnight would shift the
    # cutoff with deployment timezone — spurious 422s for users half a
    # world away from the host.
    if value is not None and value < datetime.now(UTC).date():
        raise ValueError("due_date cannot be in the past")
    return value


class TaskCreate(BaseModel):
    title: NotBlankStr = Field(min_length=1, max_length=255)
    description: SafeStr | None = Field(default=None, max_length=10000)
    status: TaskStatus = TaskStatus.TODO
    priority: TaskPriority = TaskPriority.MEDIUM
    due_date: date | None = None
    # Optional direct assignment at creation; still validated as org member.
    assigned_to_id: uuid.UUID | None = None

    _deadline_not_past = field_validator("due_date")(_validate_deadline_not_past)


class TaskUpdate(BaseModel):
    """PATCH semantics: a key ABSENT from the body means "don't touch"; an
    explicit null means "clear this field" — but only for nullable columns.
    description and due_date are clearable; title/status/priority are NOT NULL
    and reject explicit nulls at the schema gate (otherwise they'd surface as
    an IntegrityError 500 at flush time).
    """

    title: NotBlankStr | None = Field(default=None, min_length=1, max_length=255)
    description: SafeStr | None = Field(default=None, max_length=10000)
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    due_date: date | None = None
    # NOTE: no assigned_to_id here — assignment is a separate MANAGER-only
    # endpoint, so a MEMBER editing their own task can never reassign it.

    _deadline_not_past = field_validator("due_date")(_validate_deadline_not_past)

    @model_validator(mode="after")
    def _forbid_clearing_required_fields(self) -> "TaskUpdate":
        for name in ("title", "status", "priority"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"'{name}' cannot be cleared")
        return self


class AssignRequest(BaseModel):
    user_id: uuid.UUID


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    description: str | None
    status: TaskStatus
    priority: TaskPriority
    assigned_to_id: uuid.UUID | None
    due_date: date | None
    created_by_id: uuid.UUID
    created_at: datetime
    # Optimistic-locking counter; clients can send it back with future
    # If-Match-style conflict detection.
    version: int


class TaskPage(BaseModel):
    items: list[TaskResponse]
    total: int
    page: int
    limit: int
    pages: int
