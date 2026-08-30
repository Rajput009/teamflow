"""Regression tests for the Phase-1 code-review fixes."""
import uuid

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Activity, RefreshToken
from app.tests.factories import (
    create_organization,
    create_project,
    create_task,
    create_user,
)


class TestDueDateValidation:
    async def test_task_create_rejects_past_due_date(self, client: AsyncClient):
        owner = await create_user(client, email="due-owner@test.com")
        await create_organization(client, owner, "Org")
        project = await create_project(client, owner, "P")

        response = await client.post(
            f"/api/v1/projects/{project['id']}/tasks",
            headers=owner["_headers"],
            json={"title": "Backdated", "due_date": "2020-01-01"},
        )
        assert response.status_code == 422
        fields = {d["field"] for d in response.json()["error"]["details"]}
        assert "due_date" in fields

    async def test_task_update_rejects_past_due_date(self, client: AsyncClient):
        owner = await create_user(client, email="due2@test.com")
        await create_organization(client, owner, "Org")
        project = await create_project(client, owner, "P")
        task = await create_task(client, owner, uuid.UUID(project["id"]), "T")

        response = await client.patch(
            f"/api/v1/tasks/{task['id']}",
            headers=owner["_headers"],
            json={"due_date": "2020-01-01"},
        )
        assert response.status_code == 422


class TestAssignmentAudit:
    async def test_creation_time_assignment_is_audited(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await create_user(client, email="audit-owner@test.com")
        await create_organization(client, owner, "Org")
        member = await create_user(client, email="audit-member@test.com")
        added = await client.post(
            "/api/v1/organizations/members",
            headers=owner["_headers"],
            json={"email": member["user"]["email"], "role": "MEMBER"},
        )
        project = await create_project(client, owner, "P")

        task = await create_task(
            client,
            owner,
            uuid.UUID(project["id"]),
            "Assigned at birth",
            assigned_to_id=added.json()["user_id"],
        )

        row = await db_session.scalar(
            select(Activity).where(
                Activity.action == "task.assigned",
                Activity.task_id == uuid.UUID(task["id"]),
            )
        )
        assert row is not None
        assert row.new_value["assigned_to"] == added.json()["user_id"]


class TestAuditNoise:
    async def test_noop_project_patch_records_nothing_new(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner = await create_user(client, email="noop@test.com")
        await create_organization(client, owner, "Org")
        project = await create_project(client, owner, "Stable Project")

        before = await db_session.scalar(select(func.count()).select_from(Activity))
        # PATCH with the SAME values the project already has
        response = await client.patch(
            f"/api/v1/projects/{project['id']}",
            headers=owner["_headers"],
            json={"name": "Stable Project", "status": "PLANNING"},
        )
        assert response.status_code == 200
        after = await db_session.scalar(select(func.count()).select_from(Activity))
        assert after == before


class TestSessionCap:
    async def test_active_refresh_tokens_capped(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user = await create_user(client, email="flooder@test.com")

        for _ in range(15):
            login_response = await client.post(
                "/api/v1/auth/login",
                json={
                    "email": user["user"]["email"],
                    "password": user["password"],
                },
            )
            assert login_response.status_code == 200

        active_count = await db_session.scalar(
            select(func.count())
            .select_from(RefreshToken)
            .where(
                RefreshToken.user_id == uuid.UUID(user["user"]["id"]),
                RefreshToken.revoked_at.is_(None),
            )
        )
        assert active_count <= 10
