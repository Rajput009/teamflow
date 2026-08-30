import re
import secrets
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Organization


class OrganizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def slug_exists(self, slug: str) -> bool:
        stmt = select(func.count()).select_from(Organization).where(Organization.slug == slug)
        return bool(await self._session.scalar(stmt))

    async def get_by_id(self, org_id: uuid.UUID) -> Organization | None:
        return await self._session.get(Organization, org_id)

    async def create(
        self, *, name: str, description: str | None, created_by_id: uuid.UUID
    ) -> Organization:
        """Insert with a generated slug.

        The unique check + insert is racy by nature (two concurrent requests
        can both observe 'slug free'), so the insert runs inside a SAVEPOINT:
        on IntegrityError only the savepoint rolls back and we retry with a
        random suffix — the outer transaction (and its membership insert)
        stays alive.
        """
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "org"

        for attempt in range(5):
            if attempt == 0:
                # fast path: no suffix unless the base slug is visibly taken
                candidate = base
            else:
                candidate = f"{base}-{secrets.token_hex(3)}"
            if await self.slug_exists(candidate):
                continue

            org = Organization(
                name=name,
                slug=candidate,
                description=description,
                created_by_id=created_by_id,
            )
            self._session.add(org)
            try:
                async with self._session.begin_nested():
                    await self._session.flush()
            except IntegrityError:
                continue  # lost a race; retry with a fresh suffix
            return org

        from app.core.exceptions import ConflictError

        raise ConflictError(
            message="Could not generate a unique organization slug."
        )
