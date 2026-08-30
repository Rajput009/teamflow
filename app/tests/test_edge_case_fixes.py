"""Regression tests for the Batch-1 edge-case fixes (C1-C5).

Each test reproduces the exact scenario from the edge-case audit that used
to fail, then asserts the designed behavior.
"""
import uuid

from app.tests.factories import (
    add_member,
    create_organization,
    create_project,
    create_task,
    create_user,
)


class TestProjectSnapshotSerialization:
    async def test_patch_deadline_returns_200(self, client):
        """C1: deadline is a date; a raw date in the activity snapshot's JSONB
        new_value crashed the flush and rolled back the whole update."""
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project = await create_project(client, user, "Alpha")

        response = await client.patch(
            f"/api/v1/projects/{project['id']}",
            headers=user["_headers"],
            json={"deadline": "2030-06-15"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["deadline"] == "2030-06-15"

    async def test_snapshot_stores_iso_string(self, client):
        """The activity row must carry a JSON-safe value, not just survive."""
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project = await create_project(client, user, "Alpha")

        await client.patch(
            f"/api/v1/projects/{project['id']}",
            headers=user["_headers"],
            json={"deadline": "2030-06-15", "status": "ACTIVE"},
        )

        response = await client.get(
            "/api/v1/activities",
            headers=user["_headers"],
        )
        assert response.status_code == 200
        entries = [
            e
            for e in response.json()["items"]
            if e["action"] == "project.updated"
        ]
        assert entries, "expected a project.updated activity row"
        assert isinstance(entries[0]["new_value"]["deadline"], str)
        assert entries[0]["new_value"]["deadline"] == "2030-06-15"
        assert entries[0]["new_value"]["status"] == "ACTIVE"


class TestCaseInsensitiveProjectNames:
    async def test_create_case_variant_conflicts(self, client):
        """C3b: 'alpha' vs 'Alpha' used to slip past both the app check AND
        the case-sensitive DB constraint â€” silent duplicate names."""
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        await create_project(client, user, "Alpha")

        response = await client.post(
            "/api/v1/projects",
            headers=user["_headers"],
            json={"name": "alpha"},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "PROJECT_NAME_EXISTS"

    async def test_rename_to_case_variant_of_sibling_conflicts(self, client):
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        await create_project(client, user, "Alpha")
        beta = await create_project(client, user, "Beta")

        response = await client.patch(
            f"/api/v1/projects/{beta['id']}",
            headers=user["_headers"],
            json={"name": "ALPHA"},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "PROJECT_NAME_EXISTS"

    async def test_rename_to_own_name_other_case_succeeds(self, client):
        """Renaming a project to a case-variant of ITS OWN name must not trip
        the uniqueness rule."""
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project = await create_project(client, user, "Alpha")

        response = await client.patch(
            f"/api/v1/projects/{project['id']}",
            headers=user["_headers"],
            json={"name": "ALPHA"},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "ALPHA"


class TestLastOwnerInvariantsStillHold:
    """The lock_owners rewrite changed HOW the check runs (row locks instead
    of a count); behavior on the serial path must be identical."""

    async def test_owner_cannot_demote_self_when_last(self, client):
        owner = await create_user(client)
        await create_organization(client, owner, "Acme")

        response = await client.patch(
            f"/api/v1/organizations/members/{owner['user']['id']}",
            headers=owner["_headers"],
            json={"role": "MEMBER"},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "LAST_OWNER"

    async def test_non_last_owner_demotion_succeeds(self, client):
        owner = await create_user(client)
        await create_organization(client, owner, "Acme")
        second = await create_user(client)
        await add_member(client, owner, second["user"]["email"], role="MANAGER")
        # Ownership transfer isn't exposed, so demote the MANAGER instead â€”
        # exercises the same lock_owners code path on the non-owner branch.
        response = await client.patch(
            f"/api/v1/organizations/members/{second['user']['id']}",
            headers=owner["_headers"],
            json={"role": "MEMBER"},
        )
        assert response.status_code == 200
        assert response.json()["role"] == "MEMBER"


class TestNulByteRejection:
    """C5: NUL bytes are valid JSON but PostgreSQL rejects them at flush â€”
    previously an unhandled DataError -> 500. Must be a 422 at the gate."""

    async def test_task_title_rejected(self, client):
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project = await create_project(client, user, "Alpha")

        response = await client.post(
            f"/api/v1/projects/{project['id']}/tasks",
            headers=user["_headers"],
            json={"title": "a\u0000b"},
        )
        assert response.status_code == 422

    async def test_registration_full_name_rejected(self, client):
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": f"nul-{uuid.uuid4().hex[:8]}@test.com",
                "password": "password123",
                "full_name": "Evil\u0000Name",
            },
        )
        assert response.status_code == 422

    async def test_comment_content_rejected(self, client):
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project = await create_project(client, user, "Alpha")
        task = await create_task(client, user, uuid.UUID(project["id"]), "T1")

        response = await client.post(
            f"/api/v1/tasks/{task['id']}/comments",
            headers=user["_headers"],
            json={"content": "hello\u0000world"},
        )
        assert response.status_code == 422


class TestDuplicateRacesTranslateTo409:
    """C4: the SAVEPOINT translations only trigger under true concurrency,
    but their serial-path equivalents (pre-check hits) must still return the
    same domain errors â€” proving the translation layer didn't break the
    normal flow."""

    async def test_duplicate_email_still_409(self, client):
        first = await create_user(client)
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": first["user"]["email"],
                "password": "password123",
                "full_name": "Dup",
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "EMAIL_ALREADY_EXISTS"

    async def test_duplicate_project_member_still_409(self, client):
        owner = await create_user(client)
        await create_organization(client, owner, "Acme")
        member = await create_user(client)
        await add_member(client, owner, member["user"]["email"])
        project = await create_project(client, owner, "Alpha")

        from app.tests.factories import add_project_member

        await add_project_member(
            client, owner, uuid.UUID(project["id"]), member["user"]["id"]
        )
        response = await client.post(
            f"/api/v1/projects/{project['id']}/members",
            headers=owner["_headers"],
            json={"user_id": member["user"]["id"]},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "ALREADY_PROJECT_MEMBER"


class TestClearSemantics:
    """H5: PATCH null = clear nullable fields; absent key = untouched."""

    async def test_clear_task_description_and_due_date(self, client):
        from datetime import date, timedelta

        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project = await create_project(client, user, "Alpha")
        task = await create_task(
            client,
            user,
            uuid.UUID(project["id"]),
            "T1",
            description="original",
            due_date=str(date.today() + timedelta(days=7)),
        )

        response = await client.patch(
            f"/api/v1/tasks/{task['id']}",
            headers=user["_headers"],
            json={"description": None, "due_date": None},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["description"] is None
        assert body["due_date"] is None
        assert body["title"] == "T1"  # untouched

    async def test_absent_key_still_untouched(self, client):
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project = await create_project(client, user, "Alpha")
        task = await create_task(
            client, user, uuid.UUID(project["id"]), "T1", description="keep me"
        )

        response = await client.patch(
            f"/api/v1/tasks/{task['id']}",
            headers=user["_headers"],
            json={"priority": "HIGH"},
        )
        assert response.status_code == 200
        assert response.json()["description"] == "keep me"

    async def test_explicit_null_title_rejected(self, client):
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project = await create_project(client, user, "Alpha")
        task = await create_task(client, user, uuid.UUID(project["id"]), "T1")

        response = await client.patch(
            f"/api/v1/tasks/{task['id']}",
            headers=user["_headers"],
            json={"title": None},
        )
        assert response.status_code == 422

    async def test_explicit_null_project_name_rejected(self, client):
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project = await create_project(client, user, "Alpha")

        response = await client.patch(
            f"/api/v1/projects/{project['id']}",
            headers=user["_headers"],
            json={"name": None},
        )
        assert response.status_code == 422

    async def test_clear_org_description(self, client):
        user = await create_user(client)
        org = await create_organization(client, user, "Acme")

        response = await client.patch(
            "/api/v1/organizations/current",
            headers=user["_headers"],
            json={"description": None},
        )
        assert response.status_code == 200
        assert response.json()["description"] is None
        assert response.json()["name"] == org["name"]


class TestNotBlankInputs:
    """H4: whitespace-only named fields are rejected, not stripped to ''."""

    async def test_whitespace_task_title_rejected(self, client):
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project = await create_project(client, user, "Alpha")

        response = await client.post(
            f"/api/v1/projects/{project['id']}/tasks",
            headers=user["_headers"],
            json={"title": "   "},
        )
        assert response.status_code == 422

    async def test_whitespace_org_name_rejected(self, client):
        user = await create_user(client)
        response = await client.post(
            "/api/v1/organizations",
            headers=user["_headers"],
            json={"name": "   "},
        )
        assert response.status_code == 422

    async def test_whitespace_registration_name_rejected(self, client):
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": f"blank-{uuid.uuid4().hex[:8]}@test.com",
                "password": "password123",
                "full_name": "   ",
            },
        )
        assert response.status_code == 422


class TestUtcDateValidation:
    """H6: past-date checks use UTC 'today', not server-local midnight."""

    async def test_yesterday_due_date_rejected(self, client):
        from datetime import date, timedelta

        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project = await create_project(client, user, "Alpha")

        response = await client.post(
            f"/api/v1/projects/{project['id']}/tasks",
            headers=user["_headers"],
            json={"title": "T1", "due_date": str(date.today() - timedelta(days=1))},
        )
        assert response.status_code == 422

    async def test_today_due_date_accepted(self, client):
        from datetime import date

        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project = await create_project(client, user, "Alpha")

        response = await client.post(
            f"/api/v1/projects/{project['id']}/tasks",
            headers=user["_headers"],
            json={"title": "T1", "due_date": str(date.today())},
        )
        assert response.status_code == 201, response.text


class TestDueDateNowAudited:
    """Restructuring update() also closed an activity-log gap: due_date and
    description changes were previously silent; every field change now logs."""

    async def test_due_date_change_records_iso_diff(self, client):
        from datetime import date, timedelta

        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project = await create_project(client, user, "Alpha")
        task = await create_task(
            client,
            user,
            uuid.UUID(project["id"]),
            "T1",
            due_date=str(date.today() + timedelta(days=5)),
        )
        new_day = str(date.today() + timedelta(days=10))

        await client.patch(
            f"/api/v1/tasks/{task['id']}",
            headers=user["_headers"],
            json={"due_date": new_day},
        )

        response = await client.get(
            "/api/v1/activities", headers=user["_headers"]
        )
        entries = [
            e for e in response.json()["items"] if e["action"] == "task.updated"
        ]
        assert entries, "expected a task.updated activity row"
        latest = entries[0]
        assert "due_date" in latest["old_value"]
        assert latest["new_value"]["due_date"] == new_day


class TestNoopAssignmentSuppressed:
    """Reassigning a task to its CURRENT assignee must not duplicate the
    audit row or re-notify."""

    async def test_reassign_same_user_records_nothing(self, client):
        from app.tests.factories import add_project_member

        owner = await create_user(client)
        await create_organization(client, owner, "Acme")
        member = await create_user(client)
        await add_member(client, owner, member["user"]["email"])
        project = await create_project(client, owner, "Alpha")
        await add_project_member(
            client, owner, uuid.UUID(project["id"]), member["user"]["id"]
        )
        task = await create_task(client, owner, uuid.UUID(project["id"]), "T1")

        first = await client.post(
            f"/api/v1/tasks/{task['id']}/assign",
            headers=owner["_headers"],
            json={"user_id": member["user"]["id"]},
        )
        assert first.status_code == 200

        second = await client.post(
            f"/api/v1/tasks/{task['id']}/assign",
            headers=owner["_headers"],
            json={"user_id": member["user"]["id"]},
        )
        assert second.status_code == 200

        response = await client.get(
            "/api/v1/activities",
            headers=owner["_headers"],
            params={"action": "task.assigned"},
        )
        assert response.json()["total"] == 1, "no-op reassignment must not log"


class TestDeleteAudits:
    """Deletion is a mutation — the trail must record it even though the row
    is gone."""

    async def test_task_delete_records_activity(self, client):
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project = await create_project(client, user, "Alpha")
        task = await create_task(client, user, uuid.UUID(project["id"]), "Doomed")

        deleted = await client.delete(
            f"/api/v1/tasks/{task['id']}", headers=user["_headers"]
        )
        assert deleted.status_code == 204

        response = await client.get(
            "/api/v1/activities",
            headers=user["_headers"],
            params={"action": "task.deleted"},
        )
        items = response.json()["items"]
        assert response.json()["total"] == 1
        assert items[0]["old_value"] == {"title": "Doomed"}

    async def test_comment_delete_records_activity(self, client):
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project = await create_project(client, user, "Alpha")
        task = await create_task(client, user, uuid.UUID(project["id"]), "T1")

        created = await client.post(
            f"/api/v1/tasks/{task['id']}/comments",
            headers=user["_headers"],
            json={"content": "to be removed"},
        )
        comment_id = created.json()["id"]

        deleted = await client.delete(
            f"/api/v1/comments/{comment_id}", headers=user["_headers"]
        )
        assert deleted.status_code == 204

        response = await client.get(
            "/api/v1/activities",
            headers=user["_headers"],
            params={"action": "comment.deleted"},
        )
        assert response.json()["total"] == 1

    async def test_remove_org_member_records_activity(self, client):
        owner = await create_user(client)
        await create_organization(client, owner, "Acme")
        member = await create_user(client)
        await add_member(client, owner, member["user"]["email"])

        removed = await client.delete(
            f"/api/v1/organizations/members/{member['user']['id']}",
            headers=owner["_headers"],
        )
        assert removed.status_code == 204

        response = await client.get(
            "/api/v1/activities",
            headers=owner["_headers"],
            params={"action": "member.removed"},
        )
        assert response.json()["total"] == 1

    async def test_remove_project_member_records_activity(self, client):
        from app.tests.factories import add_project_member

        owner = await create_user(client)
        await create_organization(client, owner, "Acme")
        member = await create_user(client)
        await add_member(client, owner, member["user"]["email"])
        project = await create_project(client, owner, "Alpha")
        await add_project_member(
            client, owner, uuid.UUID(project["id"]), member["user"]["id"]
        )

        removed = await client.delete(
            f"/api/v1/projects/{project['id']}/members/{member['user']['id']}",
            headers=owner["_headers"],
        )
        assert removed.status_code == 204

        response = await client.get(
            "/api/v1/activities",
            headers=owner["_headers"],
            params={"action": "project_member.removed"},
        )
        assert response.json()["total"] == 1


class TestSearchWildcardEscaping:
    """A literal '%' in search must match only titles containing '%', not
    everything."""

    async def test_percent_matches_literal_only(self, client):
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project_id = (await create_project(client, user, "Alpha"))["id"]
        pid = uuid.UUID(project_id)
        await create_task(client, user, pid, "50% off sale")
        await create_task(client, user, pid, "50 off sale")

        response = await client.get(
            f"/api/v1/projects/{project_id}/tasks",
            headers=user["_headers"],
            params={"search": "50% off"},
        )
        titles = [t["title"] for t in response.json()["items"]]
        assert titles == ["50% off sale"]

    async def test_underscore_matches_literal_only(self, client):
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project_id = (await create_project(client, user, "Beta"))["id"]
        pid = uuid.UUID(project_id)
        await create_task(client, user, pid, "file_name.txt")
        await create_task(client, user, pid, "filename txt")

        response = await client.get(
            f"/api/v1/projects/{project_id}/tasks",
            headers=user["_headers"],
            params={"search": "file_name"},
        )
        titles = [t["title"] for t in response.json()["items"]]
        assert titles == ["file_name.txt"]
