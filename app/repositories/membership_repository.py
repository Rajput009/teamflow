import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyMemberError
from app.models import Membership, OrgRole, User


class MembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_user(self, org_id: uuid.UUID, user_id: uuid.UUID) -> Membership | None:
        stmt = select(Membership).where(
            Membership.organization_id == org_id, Membership.user_id == user_id
        )
        return await self._session.scalar(stmt)

    async def get_first_for_user(self, user_id: uuid.UUID) -> Membership | None:
        """Active-org resolution for V1 (single-org users). Multi-org token
        claims replace this in V2 — endpoints will not change."""
        stmt = (
            select(Membership)
            .where(Membership.user_id == user_id)
            .order_by(Membership.created_at)
            .limit(1)
        )
        return await self._session.scalar(stmt)

    async def lock_owners(self, org_id: uuid.UUID) -> list[Membership]:
        """SELECT ... FOR UPDATE over the org's owner rows.

        The last-owner invariant is a TOCTOU trap if checked with a bare
        COUNT: two owners demoting each other concurrently both read 2 and
        both proceed, leaving the org with zero owners. Locking the rows
        serializes the check against any concurrent role change — the second
        transaction re-reads committed state after acquiring the locks.
        Ordered by id so concurrent lockers acquire in the same sequence
        (no deadlock).
        """
        stmt = (
            select(Membership)
            .where(
                Membership.organization_id == org_id,
                Membership.role == OrgRole.OWNER,
            )
            .order_by(Membership.id)
            .with_for_update()
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_members(
        self, org_id: uuid.UUID, *, page: int = 1, limit: int = 20
    ) -> tuple[list[tuple[Membership, User]], int]:

        total = await self._session.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.organization_id == org_id)
        )
        stmt = (
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.organization_id == org_id)
            # id tiebreaker: identical timestamps must not shuffle page
            # boundaries between requests
            .order_by(Membership.created_at, Membership.id)
            .offset((page - 1) * limit)
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        return [(m, u) for m, u in rows], int(total or 0)

    async def create(
        self, *, organization_id: uuid.UUID, user_id: uuid.UUID, role: OrgRole
    ) -> Membership:
        """Insert inside a SAVEPOINT.

        The service-level 'already a member?' pre-check is TOCTOU-racy: two
        concurrent add-member requests both see no membership, and the loser
        hits the (organization_id, user_id) unique constraint. Without the
        savepoint that surfaces as an IntegrityError -> 500; with it, only
        the insert rolls back and we translate to the designed 409. (The
        org-creation path also calls this for the owner membership, where a
        conflict is impossible — translation is simply unreachable there.)
        """
        membership = Membership(
            organization_id=organization_id, user_id=user_id, role=role
        )
        self._session.add(membership)
        try:
            async with self._session.begin_nested():
                await self._session.flush()
        except IntegrityError:
            raise AlreadyMemberError() from None
        return membership

    async def delete(self, membership: Membership) -> None:
        await self._session.delete(membership)
        await self._session.flush()
