"""Tasks: CRUD, assignment rules, filtering, pagination — 06-task-management.md."""
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
    owner = await create_user(client, email="task-owner@test.com")
    await create_organization(client, owner, "Org")
    project = await create_project(client, owner, "Project X")

    member = await create_user(client, email="task-member@test.com")
    member_added = await add_member(client, owner, "task-member@test.com")
    # the member belongs to this project — realistic collaboration setup
    await add_project_member(
        client, owner, uuid.UUID(project["id"]), uuid.UUID(member_added["user_id"])
    )

    outsider = await create_user(client, email="outsider@test.com", full_name="Out")
    await create_organization(client, outsider, "Other Org")

    return owner, member, member_added, project, outsider


class TestTaskCreation:
    async def test_create_with_defaults(self, client: AsyncClient):
        owner, _m, _ma, project, _o = await _setup(client)
        task = await create_task(client, owner, uuid.UUID(project["id"]), "Do thing")
        assert task["status"] == "TODO"
        assert task["priority"] == "MEDIUM"
        assert task["assigned_to_id"] is None

    async def test_project_must_be_in_own_org(self, client: AsyncClient):
        owner, _m, _ma, project, rival = await _setup(client)
        response = await client.post(
            f"/api/v1/projects/{project['id']}/tasks",
            headers=rival["_headers"],
            json={"title": "Steal work"},
        )
        assert response.status_code == 404


class TestAssignment:
    async def test_assign_to_org_member(self, client: AsyncClient):
        owner, member, added, project, _o = await _setup(client)
        task = await create_task(
            client, owner, uuid.UUID(project["id"]), "Auth API"
        )

        response = await client.post(
            f"/api/v1/tasks/{task['id']}/assign",
            headers=owner["_headers"],
            json={"user_id": added["user_id"]},
        )
        assert response.status_code == 200
        assert response.json()["assigned_to_id"] == added["user_id"]

    async def test_assign_to_non_org_member_rejected(self, client: AsyncClient):
        """THE flagship cross-entity rule the DB cannot enforce."""
        owner, _m, _ma, project, outsider = await _setup(client)
        task = await create_task(
            client, owner, uuid.UUID(project["id"]), "Auth API"
        )

        response = await client.post(
            f"/api/v1/tasks/{task['id']}/assign",
            headers=owner["_headers"],
            json={"user_id": outsider["user"]["id"]},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "USER_NOT_ORG_MEMBER"

    async def test_member_cannot_assign(self, client: AsyncClient):
        owner, member, added, project, _o = await _setup(client)
        task = await create_task(
            client, owner, uuid.UUID(project["id"]), "Auth API"
        )

        response = await client.post(
            f"/api/v1/tasks/{task['id']}/assign",
            headers=member["_headers"],
            json={"user_id": added["user_id"]},
        )
        assert response.status_code == 403


class TestUpdateRules:
    async def test_member_updates_only_assigned_tasks(self, client: AsyncClient):
        owner, member, added, project, _o = await _setup(client)

        assigned = await create_task(
            client,
            owner,
            uuid.UUID(project["id"]),
            "Mine",
            assigned_to_id=added["user_id"],
        )
        unassigned = await create_task(
            client, owner, uuid.UUID(project["id"]), "Not mine"
        )

        own = await client.patch(
            f"/api/v1/tasks/{assigned['id']}",
            headers=member["_headers"],
            json={"status": "IN_REVIEW"},
        )
        foreign = await client.patch(
            f"/api/v1/tasks/{unassigned['id']}",
            headers=member["_headers"],
            json={"status": "IN_REVIEW"},
        )
        assert own.status_code == 200
        assert own.json()["status"] == "IN_REVIEW"
        assert foreign.status_code == 403

    async def test_member_cannot_reassign_via_update(self, client: AsyncClient):
        """PATCH schema has no assigned_to_id field — reassignment is a
        separate MANAGER-only operation."""
        owner, member, added, project, _o = await _setup(client)
        task = await create_task(
            client, owner, uuid.UUID(project["id"]), "T", assigned_to_id=added["user_id"]
        )

        response = await client.patch(
            f"/api/v1/tasks/{task['id']}",
            headers=member["_headers"],
            json={"assigned_to_id": str(uuid.uuid4())},
        )
        # extra fields are ignored by default Pydantic config; the field must
        # remain untouched either way
        assert response.status_code == 200
        assert response.json()["assigned_to_id"] == added["user_id"]


class TestListing:
    async def test_filters_combine(self, client: AsyncClient):
        owner, member, added, project, _o = await _setup(client)
        pid = uuid.UUID(project["id"])
        await create_task(
            client, owner, pid, "Urgent progress",
            status="IN_PROGRESS", priority="URGENT", assigned_to_id=added["user_id"],
        )
        await create_task(client, owner, pid, "Low priority", priority="LOW")
        await create_task(client, owner, pid, "Review me", status="IN_REVIEW")

        combined = await client.get(
            f"/api/v1/projects/{pid}/tasks?status=IN_PROGRESS&priority=URGENT",
            headers=owner["_headers"],
        )
        by_assignee = await client.get(
            f"/api/v1/projects/{pid}/tasks?assigned_to={added['user_id']}",
            headers=owner["_headers"],
        )
        search = await client.get(
            f"/api/v1/projects/{pid}/tasks?search=urgent", headers=owner["_headers"]
        )
        empty = await client.get(
            f"/api/v1/projects/{pid}/tasks?priority=LOW&status=COMPLETED",
            headers=owner["_headers"],
        )
        assert combined.json()["total"] == 1
        assert combined.json()["items"][0]["title"] == "Urgent progress"
        assert by_assignee.json()["total"] == 1
        assert search.json()["total"] == 1
        assert empty.json()["total"] == 0

    async def test_pagination_math(self, client: AsyncClient):
        owner, _m, _ma, project, _o = await _setup(client)
        pid = uuid.UUID(project["id"])
        for i in range(5):
            await create_task(client, owner, pid, f"Task {i}")

        page1 = await client.get(
            f"/api/v1/projects/{pid}/tasks?page=1&limit=2", headers=owner["_headers"]
        )
        page3 = await client.get(
            f"/api/v1/projects/{pid}/tasks?page=3&limit=2", headers=owner["_headers"]
        )
        body1, body3 = page1.json(), page3.json()
        assert body1["total"] == 5
        assert body1["pages"] == 3
        assert len(body1["items"]) == 2
        assert len(body3["items"]) == 1

    async def test_ordering_whitelist(self, client: AsyncClient):
        owner, _m, _ma, project, _o = await _setup(client)
        pid = uuid.UUID(project["id"])
        await create_task(client, owner, pid, "A")
        await create_task(client, owner, pid, "B")

        ok = await client.get(
            f"/api/v1/projects/{pid}/tasks?ordering=-created_at", headers=owner["_headers"]
        )
        bad = await client.get(
            f"/api/v1/projects/{pid}/tasks?ordering=password_hash--", headers=owner["_headers"]
        )
        assert ok.status_code == 200
        assert [t["title"] for t in ok.json()["items"]] == ["B", "A"]
        assert bad.status_code == 422
        assert bad.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_cross_org_task_read_is_404(self, client: AsyncClient):
        owner, _m, _ma, project, outsider = await _setup(client)
        task = await create_task(client, owner, uuid.UUID(project["id"]), "Secret task")

        response = await client.get(f"/api/v1/tasks/{task['id']}", headers=outsider["_headers"])
        assert response.status_code == 404
