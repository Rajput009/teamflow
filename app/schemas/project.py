import uuid
from datetime import UTC, date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import ProjectStatus
from app.schemas.validators import NotBlankStr, SafeStr


class ProjectBase(BaseModel):
    name: NotBlankStr | None = Field(default=None, min_length=1, max_length=255)
    description: SafeStr | None = Field(default=None, max_length=5000)
    deadline: date | None = None

    @field_validator("deadline")
    @classmethod
    def deadline_not_in_past(cls, v: date | None) -> date | None:
        # UTC cutoff — see schemas/task.py for the rationale.
        if v is not None and v < datetime.now(UTC).date():
            raise ValueError("deadline cannot be in the past")
        return v


class ProjectCreate(ProjectBase):
    name: NotBlankStr = Field(min_length=1, max_length=255)  # required on create


class ProjectUpdate(ProjectBase):
    """PATCH semantics mirror TaskUpdate: absent = untouched, null = clear.
    Only description and deadline are nullable columns; name/status reject
    explicit nulls here rather than as flush-time IntegrityErrors."""

    status: ProjectStatus | None = None

    @model_validator(mode="after")
    def _forbid_clearing_required_fields(self) -> "ProjectUpdate":
        for name in ("name", "status"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"'{name}' cannot be cleared")
        return self


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    description: str | None
    status: ProjectStatus
    deadline: date | None
    created_by_id: uuid.UUID
    created_at: datetime
