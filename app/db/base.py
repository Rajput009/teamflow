from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Formula-based constraint names. Without this, Postgres invents names like
# "projects_organization_id_3f9c8_idx" which differ between environments and
# make Alembic downgrades unreliable.
naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for every ORM model in the application."""

    metadata = MetaData(naming_convention=naming_convention)
