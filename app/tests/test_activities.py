"""Activity log — audit trail per 09-activity-log.md."""
import uuid

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Activity
from app.tests.factories import (
    add_member,
    create_organization,
    create_project,
    create_task,
    create_user,
)


async def _setup(client: AsyncClient):
    owner = await create_user(client, email="act-owner@test.com")
    org = await create_organization(client, owner, "Org")
    project = await create_project(client, owner, "Audited Project")

    outsider = await create_user(client, email="act-out@test.com", full_name="Out")
    await create_organization(client, outsider, "Elsewhere Org")

    return owner, org, project, outsider


class TestRecording:
    async def test_task_creation_is_recorded(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner, _org, project, _o = await _setup(client)
        task = await create_task(
            client, owner, uuid.UUID(project["id"]), "Trackable task"
        )

        count = await db_session.scalar(select(func.count()).select_from(Activity))
        # project.created + task.created (role updates from setup don't exist here)
        assert count == 2

        created = await db_session.scalar(
            select(Activity).where(Activity.action == "task.created")
        )
        assert created is not None
        assert str(created.entity_id) == task["id"]
        assert str(created.actor_id) == owner["user"]["id"]
        assert created.new_value["title"] == "Trackable task"

    async def test_status_change_snapshots_old_and_new(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner, _org, project, _o = await _setup(client)
        await create_user(client, email="act-member@test.com")
        await add_member(client, owner, "act-member@test.com")

        task = await create_task(
            client, owner, uuid.UUID(project["id"]), "Statusful"
        )
        before = await db_session.scalar(select(func.count()).select_from(Activity))

        await client.patch(
            f"/api/v1/tasks/{task['id']}",
            headers=owner["_headers"],
            json={"status": "IN_PROGRESS"},
        )
        row = await db_session.scalar(
            select(Activity).where(Activity.action == "task.status_changed")
        )
        assert row is not None
        assert row.old_value["status"] == "TODO"
        assert row.new_value["status"] == "IN_PROGRESS"

        # a no-op update must NOT record anything new
        await client.patch(
            f"/api/v1/tasks/{task['id']}",
            headers=owner["_headers"],
            json={"status": "IN_PROGRESS"},
        )
        after = await db_session.scalar(select(func.count()).select_from(Activity))
        assert after == before + 1

    async def test_assignment_records_both_users(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner, _org, project, _o = await _setup(client)
        await create_user(client, email="assignee@test.com")
        added = await add_member(client, owner, "assignee@test.com")
        task = await create_task(client, owner, uuid.UUID(project["id"]), "Assignable")

        await client.post(
            f"/api/v1/tasks/{task['id']}/assign",
            headers=owner["_headers"],
            json={"user_id": added["user_id"]},
        )
        row = await db_session.scalar(
            select(Activity).where(Activity.action == "task.assigned")
        )
        assert row.new_value["assigned_to"] == added["user_id"]
        assert row.old_value is None  # previously unassigned

    async def test_failed_operation_records_nothing(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """THE transactional guarantee: a rejected operation leaves no trace —
        the activity rolls back with the state change it describes."""
        owner, _org, project, outsider = await _setup(client)
        # the setup outsider HAS an org, so we reach tenancy scoping (404 path)

        response = await client.post(
            f"/api/v1/projects/{project['id']}/tasks",
            headers=outsider["_headers"],
            json={"title": "Should never exist"},
        )
        assert response.status_code == 404

        # only owner's setup actions; nothing from the failed request
        activities = (await db_session.scalars(select(Activity))).all()
        assert all(str(a.actor_id) == owner["user"]["id"] for a in activities)


class TestReading:
    async def test_org_timeline_newest_first_and_filtered(
        self, client: AsyncClient
    ):
        owner, _org, project, _o = await _setup(client)
        pid = uuid.UUID(project["id"])
        await create_task(client, owner, pid, "One")
        await create_task(client, owner, pid, "Two")

        listing = (
            await client.get("/api/v1/activities", headers=owner["_headers"])
        ).json()
        assert listing["total"] >= 3  # project + 2 tasks
        actions = [i["action"] for i in listing["items"]]
        assert actions[0] == "task.created"  # newest first

        filtered = (
            await client.get(
                "/api/v1/activities?action=project.created",
                headers=owner["_headers"],
            )
        ).json()
        assert filtered["total"] == 1

        by_actor = (
            await client.get(
                f"/api/v1/activities?actor_id={owner['user']['id']}",
                headers=owner["_headers"],
            )
        ).json()
        assert by_actor["total"] == listing["total"]

    async def test_project_scoped_timeline(self, client: AsyncClient):
        owner, _org, project, _o = await _setup(client)
        other = await create_project(client, owner, "Other Project")
        await create_task(client, owner, uuid.UUID(project["id"]), "In audited")

        scoped = (
            await client.get(
                f"/api/v1/projects/{project['id']}/activities",
                headers=owner["_headers"],
            )
        ).json()
        assert scoped["total"] == 2  # project.created + task.created
        other_scoped = (
            await client.get(
                f"/api/v1/projects/{other['id']}/activities",
                headers=owner["_headers"],
            )
        ).json()
        assert other_scoped["total"] == 1

    async def test_cross_org_project_activities_404(self, client: AsyncClient):
        owner, _org, project, outsider = await _setup(client)
        response = await client.get(
            f"/api/v1/projects/{project['id']}/activities",
            headers=outsider["_headers"],
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
