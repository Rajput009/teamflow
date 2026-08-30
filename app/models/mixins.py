from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    """Adds created_at / updated_at to a model.

    Uses clock_timestamp(), NOT now(): now() is frozen at TRANSACTION start,
    so all rows inserted within one request share identical timestamps —
    which makes created_at ordering nondeterministic. clock_timestamp()
    advances with each statement.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.clock_timestamp(),
        onupdate=func.clock_timestamp(),
        nullable=False,
    )
