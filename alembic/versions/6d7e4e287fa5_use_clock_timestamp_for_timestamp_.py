"""use clock_timestamp for timestamp defaults

Revision ID: 6d7e4e287fa5
Revises: 69a20ba28147
Create Date: 2026-08-22

now() is frozen at transaction start: every row inserted within one request
shares an identical created_at, making ORDER BY created_at nondeterministic.
clock_timestamp() advances per statement, restoring meaningful ordering.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "6d7e4e287fa5"
down_revision: Union[str, Sequence[str], None] = "69a20ba28147"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ["users", "organizations", "memberships", "refresh_tokens", "projects", "tasks"]
COLUMNS = ["created_at", "updated_at"]


def upgrade() -> None:
    for table in TABLES:
        for column in COLUMNS:
            op.alter_column(
                table,
                column,
                server_default=sa.text("clock_timestamp()"),
            )


def downgrade() -> None:
    for table in TABLES:
        for column in COLUMNS:
            op.alter_column(table, column, server_default=sa.text("now()"))
