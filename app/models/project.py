import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    literal_column,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class ProjectStatus(enum.StrEnum):
    PLANNING = "PLANNING"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        # Case-insensitive uniqueness WITHIN an org only. The app check uses
        # lower(); the DB must enforce the same rule or "Alpha"/"alpha" slip
        # through. A plain UniqueConstraint cannot express an expression.
        Index(
            "uq_projects_org_lower_name",
            "organization_id",
            func.lower(literal_column("name")),
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # The tenancy anchor: every scoped query pairs ids with this column.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus, name="project_status", native_enum=True),
        nullable=False,
        default=ProjectStatus.PLANNING,
    )
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
