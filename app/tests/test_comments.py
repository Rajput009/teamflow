"""Comments — nested under tasks, per 08-comments.md."""
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
    owner = await create_user(client, email="cmt-owner@test.com")
    await create_organization(client, owner, "Org")
    project = await create_project(client, owner, "Project")

    member = await create_user(client, email="cmt-member@test.com")
    member_added = await add_member(client, owner, "cmt-member@test.com")
    await add_project_member(
        client, owner, uuid.UUID(project["id"]), uuid.UUID(member_added["user_id"])
    )

    outsider = await create_user(client, email="cmt-out@test.com", full_name="Out")
    await create_organization(client, outsider, "Elsewhere")

    admin = await create_user(client, email="cmt-admin@test.com", full_name="Admin")
    added_admin = await add_member(client, owner, "cmt-admin@test.com", role="MEMBER")
    promote = await client.patch(
        f"/api/v1/organizations/members/{added_admin['user_id']}",
        headers=owner["_headers"],
        json={"role": "ADMIN"},
    )
    assert promote.status_code == 200

    task = await create_task(client, owner, uuid.UUID(project["id"]), "Discussed task")
    return owner, member, member_added, project, outsider, admin, task


class TestCommenting:
    async def test_post_and_list_chronologically(self, client: AsyncClient):
        owner, _m, _ma, _p, _o, _admin, task = await _setup(client)

        first = await client.post(
            f"/api/v1/tasks/{task['id']}/comments",
            headers=owner["_headers"],
            json={"content": "First message"},
        )
        second = await client.post(
            f"/api/v1/tasks/{task['id']}/comments",
            headers=owner["_headers"],
            json={"content": "Second message"},
        )
        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["author_full_name"] == owner["user"]["full_name"]

        listing = (
            await client.get(f"/api/v1/tasks/{task['id']}/comments", headers=owner["_headers"])
        ).json()
        assert [c["content"] for c in listing["items"]] == ["First message", "Second message"]

    async def test_member_comments_on_visible_task(self, client: AsyncClient):
        owner, member, _ma, _p, _o, _admin, task = await _setup(client)
        response = await client.post(
            f"/api/v1/tasks/{task['id']}/comments",
            headers=member["_headers"],
            json={"content": "Member perspective"},
        )
        assert response.status_code == 201
        assert response.json()["author_id"] == member["user"]["id"]

    async def test_foreign_org_task_is_404(self, client: AsyncClient):
        owner, _m, _ma, _p, outsider, _admin, task = await _setup(client)
        for method, path, payload in [
            ("GET", f"/api/v1/tasks/{task['id']}/comments", None),
            ("POST", f"/api/v1/tasks/{task['id']}/comments", {"content": "hi"}),
        ]:
            if method == "GET":
                response = await client.get(path, headers=outsider["_headers"])
            else:
                response = await client.post(path, headers=outsider["_headers"], json=payload)
            assert response.status_code == 404

    async def test_empty_content_rejected(self, client: AsyncClient):
        owner, _m, _ma, _p, _o, _admin, task = await _setup(client)
        response = await client.post(
            f"/api/v1/tasks/{task['id']}/comments",
            headers=owner["_headers"],
            json={"content": "   "},
        )
        # whitespace-only passes min_length but we trim; a fully-trimmed empty
        # string should be rejected as effectively empty content
        assert response.status_code == 422 or response.json()["content"].strip() != ""


class TestDeletion:
    async def test_author_deletes_own_comment(self, client: AsyncClient):
        owner, member, _ma, _p, _o, _admin, task = await _setup(client)
        created = await client.post(
            f"/api/v1/tasks/{task['id']}/comments",
            headers=member["_headers"],
            json={"content": "mine"},
        )
        comment_id = created.json()["id"]

        response = await client.delete(
            f"/api/v1/comments/{comment_id}", headers=member["_headers"]
        )
        assert response.status_code == 204

    async def test_other_member_cannot_delete_foreign_comment(self, client: AsyncClient):
        owner, member, _ma, _p, _o, admin, task = await _setup(client)
        created = await client.post(
            f"/api/v1/tasks/{task['id']}/comments",
            headers=owner["_headers"],
            json={"content": "owner's words"},
        )
        comment_id = created.json()["id"]

        response = await client.delete(
            f"/api/v1/comments/{comment_id}", headers=member["_headers"]
        )
        assert response.status_code == 403

    async def test_admin_moderates_any_comment(self, client: AsyncClient):
        owner, _m, _ma, _p, _o, admin, task = await _setup(client)
        created = await client.post(
            f"/api/v1/tasks/{task['id']}/comments",
            headers=owner["_headers"],
            json={"content": "needs moderation"},
        )
        comment_id = created.json()["id"]

        response = await client.delete(
            f"/api/v1/comments/{comment_id}", headers=admin["_headers"]
        )
        assert response.status_code == 204

    async def test_cross_org_comment_delete_is_404_not_403(self, client: AsyncClient):
        """A 403 would confirm the comment exists — leak nothing."""
        owner, _m, _ma, _p, outsider, _admin, task = await _setup(client)
        created = await client.post(
            f"/api/v1/tasks/{task['id']}/comments",
            headers=owner["_headers"],
            json={"content": "hidden from outsiders"},
        )
        comment_id = created.json()["id"]

        response = await client.delete(
            f"/api/v1/comments/{comment_id}", headers=outsider["_headers"]
        )
        assert response.status_code == 404

    async def test_nonexistent_comment_404(self, client: AsyncClient):
        owner, _m, _ma, _p, _o, _admin, _task = await _setup(client)
        response = await client.delete(
            f"/api/v1/comments/{uuid.uuid4()}", headers=owner["_headers"]
        )
        assert response.status_code == 404
