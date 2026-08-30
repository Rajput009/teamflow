"""case-insensitive unique project names per org

The old UNIQUE (organization_id, name) constraint was case-sensitive while
the application-level check used lower() — so "Alpha" and "alpha" passed the
check AND the constraint, silently duplicating names. Replace it with a
functional unique index on (organization_id, lower(name)) so the database
enforces exactly the rule the app promises, and rename races surface as a
constraint violation we can translate to PROJECT_NAME_EXISTS.

Revision ID: d4e8f0a1b2c3
Revises: 16549775deca
Create Date: 2026-08-23 10:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e8f0a1b2c3'
down_revision: Union[str, Sequence[str], None] = '16549775deca'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        op.f('uq_projects_organization_id'), 'projects', type_='unique'
    )
    op.create_index(
        'uq_projects_org_lower_name',
        'projects',
        ['organization_id', sa.text('lower(name)')],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index('uq_projects_org_lower_name', table_name='projects')
    op.create_unique_constraint(
        op.f('uq_projects_organization_id'), 'projects', ['organization_id', 'name']
    )
