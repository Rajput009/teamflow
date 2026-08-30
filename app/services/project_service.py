import uuid
from datetime import UTC, date, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AlreadyProjectMemberError,
    ForbiddenError,
    NotFoundError,
    ProjectNameExistsError,
    UserNotOrgMemberError,
)
from app.core.serialization import json_safe
from app.models import (
    ActionType,
    Membership,
    OrgRole,
    Project,
    ProjectStatus,
    User,
)
from app.repositories.activity_repository import ActivityRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.project_member_repository import ProjectMemberRepository
from app.repositories.project_repository import ProjectRepository
from app.repositories.user_repository import UserRepository
from app.services.permissions import is_manager_or_above, require_role


class ProjectService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._projects = ProjectRepository(session)
        self._project_members = ProjectMemberRepository(session)
        self._memberships = MembershipRepository(session)
        self._users = UserRepository(session)
        self._activities = ActivityRepository(session)

    # --- access helpers ---

    def _sees_all(self, actor_membership: Membership) -> bool:
        return is_manager_or_above(actor_membership)

    async def _get_visible(
        self, *, actor: User, actor_membership: Membership, project_id: uuid.UUID
    ) -> Project:
        project = await self._projects.get_accessible(
            project_id,
            actor_membership.organization_id,
            user_id=actor.id,
            sees_all=self._sees_all(actor_membership),
        )
        if project is None:
            raise NotFoundError(message="Project not found.")
        return project

    # --- project lifecycle ---

    async def create(
        self,
        *,
        actor: User,
        actor_membership: Membership,
        name: str,
        description: str | None,
        deadline: date | None,
    ) -> Project:
        require_role(
            actor_membership, {OrgRole.OWNER, OrgRole.ADMIN, OrgRole.MANAGER}
        )
        org_id = actor_membership.organization_id
        if await self._projects.name_exists_in_org(org_id, name.strip()):
            raise ProjectNameExistsError()
        project = await self._projects.create(
            org_id=org_id,
            created_by_id=actor.id,
            name=name.strip(),
            description=description,
            deadline=deadline,
        )
        # The creator can always see their own project — even a MANAGER who is
        # later demoted to MEMBER keeps access to what they built.
        await self._project_members.add(project_id=project.id, user_id=actor.id)

        self._activities.record(
            organization_id=org_id,
            actor_id=actor.id,
            action=ActionType.PROJECT_CREATED,
            entity_type="project",
            entity_id=project.id,
            project_id=project.id,
            new_value={"name": project.name},
        )
        return project

    async def get_in_org(
        self, *, actor: User, actor_membership: Membership, project_id: uuid.UUID
    ) -> Project:
        return await self._get_visible(
            actor=actor, actor_membership=actor_membership, project_id=project_id
        )

    async def list_for_org(
        self,
        *,
        actor: User,
        actor_membership: Membership,
        page: int = 1,
        limit: int = 20,
    ) -> tuple[list[Project], int]:
        return await self._projects.list_accessible(
            actor_membership.organization_id,
            user_id=actor.id,
            sees_all=self._sees_all(actor_membership),
            page=page,
            limit=limit,
        )

    async def update(
        self,
        *,
        actor: User,
        actor_membership: Membership,
        project_id: uuid.UUID,
        fields: dict[str, object],
    ) -> Project:
        """PATCH semantics via an exclude_unset dump (see TaskService.update):
        absent key = untouched, explicit None = clear (description/deadline
        only — the schema rejects nulls for name/status)."""
        project = await self._authorize_write(
            actor=actor, actor_membership=actor_membership, project_id=project_id
        )
        changes = False
        if "name" in fields:
            clean = str(fields["name"]).strip()
            if project.name != clean:
                exists = await self._projects.name_exists_in_org(
                    actor_membership.organization_id, clean
                )
                if exists and project.name.lower() != clean.lower():
                    raise ProjectNameExistsError()
                changes = True
                # The pre-check above is racy; the DB's case-insensitive
                # unique index is the authority. Flush the rename inside a
                # SAVEPOINT so a lost race rolls back only the name and we
                # translate to the designed 409 instead of a 500.
                try:
                    async with self._session.begin_nested():
                        project.name = clean
                        await self._session.flush()
                except IntegrityError:
                    raise ProjectNameExistsError() from None
        if "description" in fields:
            if project.description != fields["description"]:
                changes = True
            project.description = fields["description"]
        if "status" in fields:
            status = fields["status"]
            now = datetime.now(UTC)
            if status == ProjectStatus.ARCHIVED and project.archived_at is None:
                project.archived_at = now
            elif status != ProjectStatus.ARCHIVED:
                project.archived_at = None
            if project.status != status:
                changes = True
            project.status = status
        if "deadline" in fields and project.deadline != fields["deadline"]:
            changes = True
            project.deadline = fields["deadline"]

        await self._session.flush()

        # no-op PATCHes leave no trace — matching task update behavior.
        # json_safe: deadline is a date and status an enum; either raw value
        # would crash the JSONB serializer mid-flush (and roll back the very
        # update this row documents).
        if changes:
            self._activities.record(
                organization_id=actor_membership.organization_id,
                actor_id=actor.id,
                action=ActionType.PROJECT_UPDATED,
                entity_type="project",
                entity_id=project.id,
                project_id=project.id,
                new_value={
                    k: json_safe(getattr(project, k))
                    for k in ("name", "description", "status", "deadline")
                },
            )
        return project

    async def delete(
        self, *, actor: User, actor_membership: Membership, project_id: uuid.UUID
    ) -> None:
        project = await self._authorize_write(
            actor=actor, actor_membership=actor_membership, project_id=project_id
        )
        await self._projects.delete(project)

    async def _authorize_write(
        self, *, actor: User, actor_membership: Membership, project_id: uuid.UUID
    ) -> Project:
        """ADMIN+ edits any project; MANAGER only projects they created;
        MEMBER never — matrix row 'Update / archive / delete project'."""
        project = await self.get_in_org(
            actor=actor, actor_membership=actor_membership, project_id=project_id
        )
        if not (
            actor_membership.role in {OrgRole.OWNER, OrgRole.ADMIN}
            or (
                actor_membership.role == OrgRole.MANAGER
                and project.created_by_id == actor.id
            )
        ):
            raise ForbiddenError()
        return project

    # --- project members ---

    async def add_project_member(
        self,
        *,
        actor_membership: Membership,
        project_id: uuid.UUID,
        target_user_id: uuid.UUID,
    ) -> tuple[Project, User]:
        require_role(actor_membership, {OrgRole.OWNER, OrgRole.ADMIN, OrgRole.MANAGER})
        # Managing members requires seeing the project at all (MANAGER+ always do).
        project = await self._projects.get_in_org(
            project_id, actor_membership.organization_id
        )
        if project is None:
            raise NotFoundError(message="Project not found.")

        target_membership = await self._memberships.get_for_user(
            actor_membership.organization_id, target_user_id
        )
        if target_membership is None:
            raise UserNotOrgMemberError()

        existing = await self._project_members.is_member(project.id, target_user_id)
        if existing:
            raise AlreadyProjectMemberError()

        await self._project_members.add(project_id=project.id, user_id=target_user_id)
        target_user = await self._users.get_by_id(target_user_id)
        if target_user is None:  # unreachable while the FK holds — belt and braces
            raise NotFoundError(message="User not found.")
        self._activities.record(
            organization_id=actor_membership.organization_id,
            actor_id=actor_membership.user_id,
            action=ActionType.PROJECT_MEMBER_ADDED,
            entity_type="project_member",
            entity_id=target_user.id,
            project_id=project.id,
            new_value={"user_id": str(target_user.id)},
        )
        return project, target_user

    async def remove_project_member(
        self,
        *,
        actor_membership: Membership,
        project_id: uuid.UUID,
        target_user_id: uuid.UUID,
    ) -> None:
        require_role(actor_membership, {OrgRole.OWNER, OrgRole.ADMIN, OrgRole.MANAGER})
        project = await self._projects.get_in_org(
            project_id, actor_membership.organization_id
        )
        if project is None:
            raise NotFoundError(message="Project not found.")

        member = await self._project_members.get(project.id, target_user_id)
        if member is None:
            raise NotFoundError(message="User is not a member of this project.")
        await self._project_members.remove(member)
        self._activities.record(
            organization_id=actor_membership.organization_id,
            actor_id=actor_membership.user_id,
            action=ActionType.PROJECT_MEMBER_REMOVED,
            entity_type="project_member",
            entity_id=target_user_id,
            project_id=project.id,
            old_value={"user_id": str(target_user_id)},
        )

    async def list_project_members(
        self, *, actor: User, actor_membership: Membership, project_id: uuid.UUID
    ) -> list[tuple]:
        project = await self.get_in_org(
            actor=actor, actor_membership=actor_membership, project_id=project_id
        )
        return await self._project_members.list_members(project.id)
