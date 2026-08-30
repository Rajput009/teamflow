"""Notification inbox management — read state, isolation, filters."""
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


async def _setup_with_notification(client: AsyncClient):
    owner = await create_user(client, email="inbox-owner@test.com")
    await create_organization(client, owner, "Org")
    project = await create_project(client, owner, "Project")

    member = await create_user(client, email="inbox-member@test.com")
    added = await add_member(client, owner, "inbox-member@test.com")
    await add_project_member(
        client, owner, uuid.UUID(project["id"]), uuid.UUID(added["user_id"])
    )

    task = await create_task(
        client,
        owner,
        uuid.UUID(project["id"]),
        "Inbox filler",
        assigned_to_id=added["user_id"],
    )
    # creation-time assignment already delivered one notification to member
    return owner, member, added, task


class TestInboxManagement:
    async def test_unread_filter_and_read_all(self, client: AsyncClient):
        owner, member, added, task = await _setup_with_notification(client)

        unread = (
            await client.get(
                "/api/v1/notifications?unread=true", headers=member["_headers"]
            )
        ).json()
        assert unread["total"] == 1

        read_all = await client.post(
            "/api/v1/notifications/read-all", headers=member["_headers"]
        )
        assert read_all.status_code == 204

        unread_after = (
            await client.get(
                "/api/v1/notifications?unread=true", headers=member["_headers"]
            )
        ).json()
        assert unread_after["total"] == 0

        everything = (
            await client.get("/api/v1/notifications", headers=member["_headers"])
        ).json()
        assert everything["total"] == 1
        assert everything["items"][0]["read_at"] is not None

    async def test_mark_single_notification_read(self, client: AsyncClient):
        owner, member, added, task = await _setup_with_notification(client)
        notification_id = (
            await client.get("/api/v1/notifications", headers=member["_headers"])
        ).json()["items"][0]["id"]

        response = await client.post(
            f"/api/v1/notifications/{notification_id}/read",
            headers=member["_headers"],
        )
        assert response.status_code == 204

        unread = (
            await client.get(
                "/api/v1/notifications?unread=true", headers=member["_headers"]
            )
        ).json()
        assert unread["total"] == 0

    async def test_cannot_read_or_mark_foreign_notifications(self, client: AsyncClient):
        owner, member, added, task = await _setup_with_notification(client)
        notification_id = (
            await client.get("/api/v1/notifications", headers=member["_headers"])
        ).json()["items"][0]["id"]

        foreign_get = await client.get(
            "/api/v1/notifications", headers=owner["_headers"]
        )
        assert foreign_get.json()["total"] == 0  # owner's inbox is empty

        foreign_mark = await client.post(
            f"/api/v1/notifications/{notification_id}/read",
            headers=owner["_headers"],
        )
        assert foreign_mark.status_code == 404
        assert foreign_mark.json()["error"]["code"] == "NOT_FOUND"

    async def test_inbox_requires_authentication(self, client: AsyncClient):
        response = await client.get("/api/v1/notifications")
        assert response.status_code == 401
