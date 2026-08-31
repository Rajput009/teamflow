import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class ChatSession(TimestampMixin, Base):
    """One conversation thread. Owner + project + org scoped.

    This is the server-owned short-term memory (L1/L2). The client never supplies
    history after Phase 1; the server owns it, caps it, and summarises the part
    that falls out of the tail.
    """

    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index("ix_chat_sessions_owner_updated", "organization_id", "user_id", "updated_at"),
        Index("ix_chat_sessions_project", "project_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Watermark: last message already folded into `summary`. Used by the
    # summarizer to determine `to_fold`; the FK is added by the migration after
    # both tables exist (circular reference between sessions and messages).
    summarized_upto_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ChatMessage(Base):
    """One stored turn. Append-only; ordering is by `seq`, never `created_at`
    (same-millisecond ties are real). `meta` stores model/usage/latency for
    debugging — it is NOT read into the prompt in Phase 1.
    """

    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_chat_messages_session_id_seq"),
        Index("ix_chat_messages_session_seq", "session_id", "seq"),
        CheckConstraint("role IN ('user', 'assistant')", name="ck_chat_messages_role"),
        CheckConstraint("btrim(content) <> ''", name="ck_chat_messages_content"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), nullable=False
    )
