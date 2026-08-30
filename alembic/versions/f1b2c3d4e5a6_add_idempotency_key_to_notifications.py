"""add idempotency_key to notifications for dedupe

Duplicate notifications could arrive from Celery retries or repeated events.
An idempotency key (event-scoped) lets the worker skip an already-delivered
notification instead of creating a second inbox entry.

Revision ID: f1b2c3d4e5a6
Revises: e7c2f8a9d1b4
Create Date: 2026-08-24 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1b2c3d4e5a6'
down_revision: Union[str, Sequence[str], None] = 'e7c2f8a9d1b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'notifications',
        sa.Column('idempotency_key', sa.String(length=255), nullable=True),
    )
    op.create_index(
        'ix_notifications_idempotency_key',
        'notifications',
        ['idempotency_key'],
    )


def downgrade() -> None:
    op.drop_index('ix_notifications_idempotency_key', table_name='notifications')
    op.drop_column('notifications', 'idempotency_key')
