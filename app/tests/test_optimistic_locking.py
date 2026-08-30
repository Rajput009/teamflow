"""Optimistic-locking tests: concurrent writers must collide loudly (409),
never silently last-write-win."""
import uuid

from sqlalchemy import update

from app.models import Task
from app.tests.factories import (
    create_organization,
    create_project,
    create_task,
    create_user,
)


class TestOptimisticLocking:
    async def test_new_tasks_start_at_version_one(self, client):
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project = await create_project(client, user, "Alpha")

        task = await create_task(client, user, uuid.UUID(project["id"]), "T1")
        assert task["version"] == 1

    async def test_successful_patch_bumps_version(self, client):
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project = await create_project(client, user, "Alpha")
        task = await create_task(client, user, uuid.UUID(project["id"]), "T1")

        response = await client.patch(
            f"/api/v1/tasks/{task['id']}",
            headers=user["_headers"],
            json={"priority": "HIGH"},
        )
        assert response.status_code == 200
        assert response.json()["version"] == 2

    async def test_lost_race_returns_409_not_silent_overwrite(self, client, db_session):
        """Simulate a second writer who commits first. We must HOLD a loaded
        copy of the task first — an unreferenced ORM instance gets evicted
        from the weak identity map and the next SELECT would silently load
        fresh state, which is exactly what a real losing writer avoids by
        having read the row before the winner committed."""
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project = await create_project(client, user, "Alpha")
        task = await create_task(client, user, uuid.UUID(project["id"]), "Contested")

        from sqlalchemy import select

        # Load through the session and KEEP the reference: this pins the
        # stale instance (version=1) in the identity map.
        stale = await db_session.scalar(
            select(Task).where(Task.id == uuid.UUID(task["id"]))
        )
        assert stale is not None and stale.version == 1

        await db_session.execute(
            update(Task)
            .where(Task.id == uuid.UUID(task["id"]))
            .values(version=Task.version + 1)
            .execution_options(synchronize_session=False)
        )

        # The pinned instance must still believe version=1
        assert stale.version == 1

        response = await client.patch(
            f"/api/v1/tasks/{task['id']}",
            headers=user["_headers"],
            json={"title": "my change"},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CONFLICT"
