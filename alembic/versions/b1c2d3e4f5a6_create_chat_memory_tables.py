"""create chat_sessions and chat_messages (Phase 1 AI short-term memory)

Creates the server-owned conversation store. Key properties (see
docs/features/13-ai-memory.md):

- every row is org+project+user scoped; repositories must always filter by org
  (and user for owner-private queries)
- order is by `seq`, not `created_at`
- the summary watermark is a message ID (not a timestamp) so same-millisecond or
  replayed messages cannot be double-folded or skipped
- the watermark FK is circular (session -> message), so it is added AFTER both
  tables exist
- role/content CHECK constraints are enforced at the DB, not only in the service
- `meta` is JSONB for model/client/latency debugging and is not read into the
  prompt in Phase 1

Revision ID: b1c2d3e4f5a6
Revises: f1b2c3d4e5a6
Create Date: 2026-08-31 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "f1b2c3d4e5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # chat_sessions first, WITHOUT the watermark FK (circular dependency).
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("summarized_upto_message_id", sa.UUID(), nullable=True),
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_chat_sessions_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_chat_sessions_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_chat_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_sessions")),
    )
    op.create_index(
        "ix_chat_sessions_owner_updated",
        "chat_sessions",
        ["organization_id", "user_id", "updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_chat_sessions_project", "chat_sessions", ["project_id", "updated_at"], unique=False
    )

    # chat_messages second (references chat_sessions).
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(content) <> ''", name=op.f("ck_chat_messages_content")
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')", name=op.f("ck_chat_messages_role")
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            name=op.f("fk_chat_messages_session_id_chat_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_messages")),
        sa.UniqueConstraint(
            "session_id", "seq", name=op.f("uq_chat_messages_session_id_seq")
        ),
    )
    op.create_index(
        "ix_chat_messages_session_seq",
        "chat_messages",
        ["session_id", "seq"],
        unique=False,
    )

    # Circular watermark FK, added now that both tables exist.
    op.create_foreign_key(
        "fk_chat_sessions_summarized_upto_message_id_chat_messages",
        "chat_sessions",
        "chat_messages",
        ["summarized_upto_message_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_chat_sessions_summarized_upto_message_id_chat_messages",
        "chat_sessions",
        type_="foreignkey",
    )
    op.drop_index("ix_chat_messages_session_seq", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_sessions_project", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_owner_updated", table_name="chat_sessions")
    op.drop_table("chat_sessions")
