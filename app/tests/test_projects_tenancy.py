"""Projects CRUD + the multi-tenancy isolation guarantees."""
from uuid import uuid4

from httpx import AsyncClient

from app.tests.factories import (
    add_member,
    create_organization,
    create_project,
    create_user,
)


async def _two_orgs(client: AsyncClient):
    """Returns (acme_owner, acme_member, acme_project, rival)."""
    owner = await create_user(client, email="acme-owner@test.com")
    await create_organization(client, owner, "Acme")
    project = await create_project(client, owner, "E-commerce Website")

    member = await create_user(client, email="acme-member@test.com")
    await add_member(client, owner, "acme-member@test.com")

    rival = await create_user(client, email="rival@test.com", full_name="Rival Inc")
    await create_organization(client, rival, "Rival Corp")

    return owner, member, project, rival


class TestProjectCreation:
    async def test_owner_creates_project(self, client: AsyncClient):
        user = await create_user(client)
        await create_organization(client, user, "Org")

        response = await client.post(
            "/api/v1/projects",
            headers=user["_headers"],
            json={"name": "Website", "deadline": "2099-10-30"},
        )
        body = response.json()
        assert response.status_code == 201
        assert body["status"] == "PLANNING"
        assert body["deadline"] == "2099-10-30"

    async def test_duplicate_name_case_insensitive(self, client: AsyncClient):
        user = await create_user(client)
        await create_organization(client, user, "Org")
        await create_project(client, user, "E-commerce Website")

        response = await client.post(
            "/api/v1/projects",
            headers=user["_headers"],
            json={"name": "e-commerce WEBSITE"},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "PROJECT_NAME_EXISTS"

    async def test_past_deadline_rejected(self, client: AsyncClient):
        user = await create_user(client)
        await create_organization(client, user, "Org")
        response = await client.post(
            "/api/v1/projects",
            headers=user["_headers"],
            json={"name": "Time Travel", "deadline": "2020-01-01"},
        )
        assert response.status_code == 422

    async def test_member_cannot_create(self, client: AsyncClient):
        owner, member, _p, _r = await _two_orgs(client)
        response = await client.post(
            "/api/v1/projects",
            headers=member["_headers"],
            json={"name": "Shadow Project"},
        )
        assert response.status_code == 403


class TestTenancyIsolation:
    async def test_cross_org_read_is_404_indistinguishable_from_missing(
        self, client: AsyncClient
    ):
        owner, _member, project, rival = await _two_orgs(client)

        foreign = await client.get(f"/api/v1/projects/{project['id']}", headers=rival["_headers"])
        missing = await client.get(f"/api/v1/projects/{uuid4()}", headers=owner["_headers"])

        assert foreign.status_code == missing.status_code == 404
        # byte-identical bodies — existence never leaks
        assert foreign.json() == missing.json()

    async def test_list_never_contains_foreign_rows(self, client: AsyncClient):
        _owner, _member, _project, rival = await _two_orgs(client)

        listing = await client.get("/api/v1/projects", headers=rival["_headers"])
        assert listing.status_code == 200
        assert listing.json()["items"] == []

    async def test_cross_org_update_and_delete_are_404(self, client: AsyncClient):
        _owner, _member, project, rival = await _two_orgs(client)

        patch = await client.patch(
            f"/api/v1/projects/{project['id']}",
            headers=rival["_headers"],
            json={"name": "Hacked"},
        )
        delete = await client.delete(f"/api/v1/projects/{project['id']}", headers=rival["_headers"])
        assert patch.status_code == 404
        assert delete.status_code == 404


class TestUpdateRules:
    async def test_projects_list_pagination(self, client: AsyncClient):
        owner = await create_user(client, email="pager@test.com")
        await create_organization(client, owner, "Org")
        for i in range(3):
            await create_project(client, owner, f"Project {i}")

        page1 = await client.get(
            "/api/v1/projects?page=1&limit=2", headers=owner["_headers"]
        )
        body = page1.json()
        assert body["total"] == 3
        assert body["pages"] == 2
        assert len(body["items"]) == 2
        # newest first: Project 2 and Project 1
        assert [p["name"] for p in body["items"]] == ["Project 2", "Project 1"]

        page2 = await client.get(
            "/api/v1/projects?page=2&limit=2", headers=owner["_headers"]
        )
        assert len(page2.json()["items"]) == 1

    async def test_manager_can_only_edit_own_projects(self, client: AsyncClient):
        owner, _member, project, _rival = await _two_orgs(client)

        manager = await create_user(client, email="manager@test.com")
        await add_member(client, owner, "manager@test.com", role="MANAGER")
        own_project = await create_project(client, manager, "Manager's Own")

        foreign_patch = await client.patch(
            f"/api/v1/projects/{project['id']}",
            headers=manager["_headers"],
            json={"name": "Not Mine"},
        )
        own_patch = await client.patch(
            f"/api/v1/projects/{own_project['id']}",
            headers=manager["_headers"],
            json={"status": "ACTIVE"},
        )
        assert foreign_patch.status_code == 403
        assert own_patch.status_code == 200
        assert own_patch.json()["status"] == "ACTIVE"

    async def test_owner_updates_anything(self, client: AsyncClient):
        owner, _member, project, _rival = await _two_orgs(client)
        response = await client.patch(
            f"/api/v1/projects/{project['id']}",
            headers=owner["_headers"],
            json={"status": "ACTIVE", "description": "updated"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ACTIVE"
