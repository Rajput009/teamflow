"""Project membership — visibility narrowing per 07-project-membership.md."""
import uuid

from httpx import AsyncClient

from app.tests.factories import (
    add_member,
    add_project_member,
    create_organization,
    create_project,
    create_task,
    create_user,
)


async def _setup(client: AsyncClient):
    owner = await create_user(client, email="pm-owner@test.com")
    await create_organization(client, owner, "Org")
    project = await create_project(client, owner, "Visible Project")
    hidden_project = await create_project(client, owner, "Hidden Project")

    member = await create_user(client, email="pm-member@test.com")
    added = await add_member(client, owner, "pm-member@test.com")

    outsider = await create_user(client, email="pm-out@test.com", full_name="Outsider")
    await create_organization(client, outsider, "Elsewhere Org")

    return owner, member, added, project, hidden_project, outsider


class TestManagingMembers:
    async def test_add_org_member_to_project(self, client: AsyncClient):
        owner, _m, added, project, _hp, _o = await _setup(client)
        response = await client.post(
            f"/api/v1/projects/{project['id']}/members",
            headers=owner["_headers"],
            json={"user_id": added["user_id"]},
        )
        assert response.status_code == 201
        assert response.json()["user_id"] == added["user_id"]

    async def test_duplicate_project_member_conflict(self, client: AsyncClient):
        owner, _m, added, project, _hp, _o = await _setup(client)
        await add_project_member(
            client, owner, uuid.UUID(project["id"]), uuid.UUID(added["user_id"])
        )
        again = await client.post(
            f"/api/v1/projects/{project['id']}/members",
            headers=owner["_headers"],
            json={"user_id": added["user_id"]},
        )
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "ALREADY_PROJECT_MEMBER"

    async def test_cannot_add_non_org_member(self, client: AsyncClient):
        """Org membership is the source of truth — project access is a subset."""
        owner, _m, _a, project, _hp, outsider = await _setup(client)
        response = await client.post(
            f"/api/v1/projects/{project['id']}/members",
            headers=owner["_headers"],
            json={"user_id": outsider["user"]["id"]},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "USER_NOT_ORG_MEMBER"

    async def test_member_cannot_manage_project_members(self, client: AsyncClient):
        owner, member, added, project, _hp, _o = await _setup(client)
        candidate = await create_user(client, email="candidate@test.com")
        await add_member(client, owner, "candidate@test.com")

        response = await client.post(
            f"/api/v1/projects/{project['id']}/members",
            headers=member["_headers"],
            json={"user_id": candidate["user"]["id"]},
        )
        assert response.status_code == 403


class TestVisibilityNarrowing:
    async def test_member_sees_only_their_projects_in_list(
        self, client: AsyncClient, db_session
    ):
        owner, member, added, project, hidden, _o = await _setup(client)
        # grant membership to the FIRST project only
        from sqlalchemy import select

        from app.models import Project

        proj = await db_session.scalar(
            select(Project).where(Project.name == "Visible Project")
        )
        await add_project_member(
            client, owner, proj.id, uuid.UUID(added["user_id"])
        )

        listing = (
            await client.get("/api/v1/projects", headers=member["_headers"])
        ).json()
        names = [p["name"] for p in listing["items"]]
        assert names == ["Visible Project"]

        detail_hidden = await client.get(
            f"/api/v1/projects/{hidden['id']}", headers=member["_headers"]
        )
        detail_visible = await client.get(
            f"/api/v1/projects/{project['id']}", headers=member["_headers"]
        )
        assert detail_hidden.status_code == 404
        assert detail_visible.status_code == 200

    async def test_manager_sees_all_org_projects_without_membership_rows(
        self, client: AsyncClient
    ):
        owner, _m, _a, project, hidden, _o = await _setup(client)
        manager = await create_user(client, email="sees-all@test.com")
        await add_member(client, owner, "sees-all@test.com", role="MANAGER")

        listing = (
            await client.get("/api/v1/projects", headers=manager["_headers"])
        ).json()
        assert len(listing["items"]) == 2

    async def test_removed_member_loses_access_immediately(self, client: AsyncClient):
        owner, member, added, project, _hp, _o = await _setup(client)
        await add_project_member(
            client, owner, uuid.UUID(project["id"]), uuid.UUID(added["user_id"])
        )

        remove = await client.delete(
            f"/api/v1/projects/{project['id']}/members/{added['user_id']}",
            headers=owner["_headers"],
        )
        assert remove.status_code == 204

        # the removed MEMBER loses visibility immediately
        after = await client.get(
            f"/api/v1/projects/{project['id']}", headers=member["_headers"]
        )
        assert after.status_code == 404

    async def test_non_member_task_read_blocked_but_managers_pass(
        self, client: AsyncClient
    ):
        owner, member, added, project, hidden, _o = await _setup(client)

        visible_task = await create_task(
            client, owner, uuid.UUID(project["id"]), "On visible"
        )
        hidden_task = await create_task(
            client, owner, uuid.UUID(hidden["id"]), "On hidden"
        )

        await add_project_member(
            client, owner, uuid.UUID(project["id"]), uuid.UUID(added["user_id"])
        )

        can_see = await client.get(
            f"/api/v1/tasks/{visible_task['id']}", headers=member["_headers"]
        )
        cannot = await client.get(
            f"/api/v1/tasks/{hidden_task['id']}", headers=member["_headers"]
        )
        manager_view = await create_user(
            client, email="mgr-view@test.com", full_name="Mgr"
        )
        await add_member(client, owner, "mgr-view@test.com", role="MANAGER")
        manager_can = await client.get(
            f"/api/v1/tasks/{hidden_task['id']}", headers=manager_view["_headers"]
        )
        assert can_see.status_code == 200
        assert cannot.status_code == 404
        assert manager_can.status_code == 200
