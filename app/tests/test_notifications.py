"""Notifications — async delivery via eager Celery, per 10-notifications.md."""
import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.tests.factories import (
    add_member,
    add_project_member,
    create_organization,
    create_project,
    create_task,
    create_user,
)


async def _setup(client: AsyncClient):
    owner = await create_user(client, email="ntf-owner@test.com", full_name="Owner")
    await create_organization(client, owner, "Org")
    project = await create_project(client, owner, "Project")

    member = await create_user(client, email="ntf-member@test.com", full_name="Member")
    added = await add_member(client, owner, "ntf-member@test.com")
    await add_project_member(
        client, owner, uuid.UUID(project["id"]), uuid.UUID(added["user_id"])
    )

    return owner, member, added


class TestDelivery:
    async def test_assignment_notifies_assignee(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner, member, added = await _setup(client)
        project_list = (
            await client.get("/api/v1/projects", headers=owner["_headers"])
        ).json()
        pid = uuid.UUID(project_list["items"][0]["id"])

        task = await create_task(
            client, owner, pid, "Notifiable work"
        )
        assign = await client.post(
            f"/api/v1/tasks/{task['id']}/assign",
            headers=owner["_headers"],
            json={"user_id": added["user_id"]},
        )
        assert assign.status_code == 200

        # eager mode: the notification EXISTS immediately after the response
        inbox = (
            await client.get(
                "/api/v1/notifications", headers=member["_headers"]
            )
        ).json()
        assert inbox["total"] == 1
        notification = inbox["items"][0]
        assert notification["type"] == "TASK_ASSIGNED"
        assert notification["read_at"] is None

    async def test_self_assignment_produces_no_notification(self, client: AsyncClient):
        owner, member, added = await _setup(client)
        project_list = (
            await client.get("/api/v1/projects", headers=owner["_headers"])
        ).json()
        pid = uuid.UUID(project_list["items"][0]["id"])
        task = await create_task(client, owner, pid, "Self work")

        response = await client.post(
            f"/api/v1/tasks/{task['id']}/assign",
            headers=owner["_headers"],
            json={"user_id": owner["user"]["id"]},
        )
        assert response.status_code == 200

        inbox_owner = (
            await client.get("/api/v1/notifications", headers=owner["_headers"])
        ).json()
        inbox_member = (
            await client.get("/api/v1/notifications", headers=member["_headers"])
        ).json()
        assert inbox_owner["total"] == 0
        assert inbox_member["total"] == 0

    async def test_comment_notifies_assignee_but_not_self(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        owner, member, added = await _setup(client)
        project_list = (
            await client.get("/api/v1/projects", headers=owner["_headers"])
        ).json()
        pid = uuid.UUID(project_list["items"][0]["id"])
        task = await create_task(
            client,
            owner,
            pid,
            "Comment target",
            assigned_to_id=added["user_id"],
        )

        # OWNER comments on member's task → member notified (assignment was at
        # creation; that already produced one TASK_ASSIGNED for member)
        comment = await client.post(
            f"/api/v1/tasks/{task['id']}/comments",
            headers=owner["_headers"],
            json={"content": "Take a look please"},
        )
        assert comment.status_code == 201

        inbox_member = (
            await client.get("/api/v1/notifications", headers=member["_headers"])
        ).json()
        types = [n["type"] for n in inbox_member["items"]]
        assert "COMMENT_ADDED" in types

        # member comments on own assigned task → no new notification for anyone
        total_before_owner = (
            await client.get("/api/v1/notifications", headers=owner["_headers"])
        ).json()["total"]
        self_comment = await client.post(
            f"/api/v1/tasks/{task['id']}/comments",
            headers=member["_headers"],
            json={"content": "On it"},
        )
        assert self_comment.status_code == 201
        total_after_owner = (
            await client.get("/api/v1/notifications", headers=owner["_headers"])
        ).json()["total"]
        assert total_after_owner == total_before_owner

    async def test_comment_on_unassigned_task_notifies_nobody(self, client: AsyncClient):
        owner, member, _added = await _setup(client)
        project_list = (
            await client.get("/api/v1/projects", headers=owner["_headers"])
        ).json()
        pid = uuid.UUID(project_list["items"][0]["id"])
        unassigned = await create_task(client, owner, pid, "Nobody's task")

        response = await client.post(
            f"/api/v1/tasks/{unassigned['id']}/comments",
            headers=member["_headers"],
            json={"content": "Just visiting"},
        )
        assert response.status_code == 201

        for headers in (owner["_headers"], member["_headers"]):
            inbox = (
                await client.get("/api/v1/notifications", headers=headers)
            ).json()
            assert inbox["total"] == 0
