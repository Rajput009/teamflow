"""add version column for optimistic locking on tasks

Concurrent PATCHes on the same task were silent last-write-wins, and the
audit trail computed diffs from stale reads. A version counter lets the ORM
emit UPDATE ... WHERE id = X AND version = N; when another writer committed
first, zero rows match and SQLAlchemy raises StaleDataError — translated to
an explicit 409 instead of a silent overwrite.

Revision ID: e7c2f8a9d1b4
Revises: d4e8f0a1b2c3
Create Date: 2026-08-23 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7c2f8a9d1b4'
down_revision: Union[str, Sequence[str], None] = 'd4e8f0a1b2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'tasks',
        sa.Column('version', sa.Integer(), nullable=False, server_default=sa.text('1')),
    )


def downgrade() -> None:
    op.drop_column('tasks', 'version')
