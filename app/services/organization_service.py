import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AlreadyMemberError,
    ForbiddenError,
    ForbiddenRoleChangeError,
    LastOwnerError,
    NoOrganizationError,
    NotFoundError,
    UserNotFoundError,
)
from app.models import ActionType, Membership, Organization, OrgRole, User
from app.repositories.activity_repository import ActivityRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.user_repository import UserRepository

# Roles that may be granted through the members API. OWNER is only ever
# assigned implicitly (org creator); ownership transfer is a future feature.
_ASSIGNABLE_ROLES = {OrgRole.MANAGER, OrgRole.MEMBER}


class OrganizationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._orgs = OrganizationRepository(session)
        self._memberships = MembershipRepository(session)
        self._activities = ActivityRepository(session)
        self._users = UserRepository(session)

    # --- organization lifecycle ---

    async def create(self, *, actor: User, name: str, description: str | None) -> Organization:
        """Create an org AND the creator's OWNER membership.

        Both writes happen in the same request transaction (committed by the
        get_db dependency) — an ownerless organization must be impossible.
        """
        org = await self._orgs.create(
            name=name.strip(),
            description=description,
            created_by_id=actor.id,
        )
        await self._memberships.create(
            organization_id=org.id, user_id=actor.id, role=OrgRole.OWNER
        )
        return org

    async def get_for_actor(self, actor: User) -> tuple[Organization, Membership]:
        return await self._resolve_context(actor)

    async def update(
        self, *, actor_membership: Membership, fields: dict[str, object]
    ) -> Organization:
        """PATCH semantics via an exclude_unset dump (see TaskService.update):
        absent key = untouched; explicit null clears description (the schema
        rejects a null name)."""
        self._require_role(actor_membership, {OrgRole.OWNER, OrgRole.ADMIN})
        org = await self._orgs.get_by_id(actor_membership.organization_id)
        if org is None:  # unreachable while the FK holds — belt and braces
            raise NoOrganizationError()
        if "name" in fields:
            org.name = str(fields["name"]).strip()
        if "description" in fields:
            org.description = fields["description"]
        await self._session.flush()
        return org

    async def delete(self, *, actor_membership: Membership) -> None:
        self._require_role(actor_membership, {OrgRole.OWNER})
        org = await self._orgs.get_by_id(actor_membership.organization_id)
        if org is None:
            raise NoOrganizationError()
        await self._session.delete(org)  # cascades to memberships/projects/...
        await self._session.flush()

    # --- members ---

    async def get_user(self, user_id: uuid.UUID) -> User:
        user = await self._users.get_by_id(user_id)
        if user is None:  # unreachable while the FK holds — belt and braces
            raise NotFoundError(message="User not found.")
        return user

    async def list_members(
        self, *, actor: User, page: int = 1, limit: int = 20
    ) -> tuple[list[tuple[Membership, User]], int]:
        org, _ = await self._resolve_context(actor)
        return await self._memberships.list_members(org.id, page=page, limit=limit)

    async def add_member(
        self,
        *,
        actor_membership: Membership,
        email: str,
        role: OrgRole,
    ) -> tuple[Membership, User]:
        self._require_role(actor_membership, {OrgRole.OWNER, OrgRole.ADMIN})
        if role not in _ASSIGNABLE_ROLES:
            raise ForbiddenError(
                message=f"Role '{role.value}' cannot be assigned directly."
            )

        target = await self._users.get_by_email(email.strip().lower())
        if target is None:
            raise UserNotFoundError()

        existing = await self._memberships.get_for_user(
            actor_membership.organization_id, target.id
        )
        if existing is not None:
            raise AlreadyMemberError()

        membership = await self._memberships.create(
            organization_id=actor_membership.organization_id, user_id=target.id, role=role
        )
        return membership, target

    async def change_role(
        self, *, actor_membership: Membership, target_user_id: uuid.UUID, new_role: OrgRole
    ) -> Membership:
        self._require_role(actor_membership, {OrgRole.OWNER, OrgRole.ADMIN})

        target = await self._get_membership_or_404(target_user_id, actor_membership)

        if new_role == OrgRole.OWNER:
            raise ForbiddenError(message="Ownership transfer is not supported yet.")

        # Only owners may touch owners (an admin demoting an owner would be
        # a privilege escalation).
        if target.role == OrgRole.OWNER and actor_membership.role != OrgRole.OWNER:
            raise ForbiddenRoleChangeError()

        if target.role == OrgRole.OWNER and new_role != OrgRole.OWNER:
            # Row-lock the owners: a bare COUNT races with concurrent
            # demotions (two owners demote each other -> zero owners).
            owners = await self._memberships.lock_owners(
                actor_membership.organization_id
            )
            if len(owners) == 1:
                raise LastOwnerError()

        previous_role = target.role
        target.role = new_role
        await self._session.flush()

        self._activities.record(
            organization_id=actor_membership.organization_id,
            actor_id=actor_membership.user_id,
            action=ActionType.MEMBER_ROLE_UPDATED,
            entity_type="membership",
            entity_id=target.id,
            old_value={"role": previous_role.value},
            new_value={"role": new_role.value},
        )
        return target

    async def remove_member(
        self, *, actor_membership: Membership, target_user_id: uuid.UUID
    ) -> None:
        self._require_role(actor_membership, {OrgRole.OWNER, OrgRole.ADMIN})

        target = await self._get_membership_or_404(target_user_id, actor_membership)

        if target.role == OrgRole.OWNER:
            if actor_membership.role != OrgRole.OWNER:
                raise ForbiddenRoleChangeError()
            # Same row-lock discipline as change_role — see lock_owners.
            owners = await self._memberships.lock_owners(
                actor_membership.organization_id
            )
            if len(owners) == 1:
                raise LastOwnerError()

        await self._memberships.delete(target)
        self._activities.record(
            organization_id=actor_membership.organization_id,
            actor_id=actor_membership.user_id,
            action=ActionType.MEMBER_REMOVED,
            entity_type="membership",
            entity_id=target.id,
            old_value={"role": target.role.value},
        )

    # --- helpers ---

    async def _resolve_context(self, actor: User) -> tuple[Organization, Membership]:
        membership = await self._memberships.get_first_for_user(actor.id)
        if membership is None:
            raise NoOrganizationError()
        org = await self._orgs.get_by_id(membership.organization_id)
        if org is None:
            raise NoOrganizationError()
        return org, membership

    async def _get_membership_or_404(
        self, target_user_id: uuid.UUID, actor_membership: Membership
    ) -> Membership:
        target = await self._memberships.get_for_user(
            actor_membership.organization_id, target_user_id
        )
        if target is None:
            raise NotFoundError(message="Member not found in this organization.")
        return target

    @staticmethod
    def _require_role(membership: Membership, allowed: set[OrgRole]) -> None:
        if membership.role not in allowed:
            raise ForbiddenError()
