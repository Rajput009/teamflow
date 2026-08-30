"""AI Project Summarizer tests (docs/features/12-ai-project-summarizer.md).

The core property under test: the STATS are database facts computed by
repositories; the LLM only supplies the prose. Tests seed known data and
assert the numbers exactly.
"""
import uuid

import httpx
from sqlalchemy import text, update

from app.models import Task
from app.tests.factories import (
    add_member,
    create_organization,
    create_project,
    create_task,
    create_user,
)

CANNED_SUMMARY = "## Status\n\nDevelopment is on track. **43% complete.**"


class TestProjectSummary:
    async def _seed_project(self, client, name: str = "Alpha"):
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project = await create_project(client, user, name)
        return user, uuid.UUID(project["id"])

    async def test_stats_are_exact_database_facts(self, client, fake_llm):
        from datetime import date, timedelta

        user, pid = await self._seed_project(client)
        today = date.today()
        await create_task(client, user, pid, "Done A", status="COMPLETED")
        await create_task(client, user, pid, "Done B", status="COMPLETED")
        await create_task(client, user, pid, "Done C", status="COMPLETED")
        await create_task(
            client,
            user,
            pid,
            "Due soon",
            priority="HIGH",
            due_date=str(today + timedelta(days=3)),
        )
        fake_llm.queue(CANNED_SUMMARY)

        response = await client.get(
            f"/api/v1/ai/projects/{pid}/summary", headers=user["_headers"]
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["project_name"] == "Alpha"
        stats = body["stats"]
        assert stats["total_tasks"] == 4
        assert stats["status_counts"]["COMPLETED"] == 3
        assert stats["progress_pct"] == 75
        assert stats["overdue_count"] == 0
        assert stats["due_within_week_count"] == 1
        # "Due soon" is HIGH and unassigned
        assert stats["unassigned_high_urgent_count"] == 1
        assert body["summary"].startswith("## Status")

        # the model received the facts — and only the facts
        sent_context = fake_llm.calls[0][1]
        assert '"total_tasks": 4' in sent_context

    async def test_overdue_detected_via_backdated_row(
        self, client, fake_llm, db_session
    ):
        """Schema validators rightly refuse past due dates at input time, so
        the overdue path is exercised by backdating an existing row directly —
        which is also exactly what 'time passed' looks like in the data."""
        user, pid = await self._seed_project(client)
        task = await create_task(client, user, pid, "Late work")

        yesterday = (
            await db_session.execute(text("SELECT now()::date - INTERVAL '1 day'"))
        ).scalar()
        await db_session.execute(
            update(Task)
            .where(Task.id == uuid.UUID(task["id"]))
            .values(due_date=yesterday)
            .execution_options(synchronize_session=False)
        )
        fake_llm.queue(CANNED_SUMMARY)

        response = await client.get(
            f"/api/v1/ai/projects/{pid}/summary", headers=user["_headers"]
        )
        assert response.status_code == 200
        assert response.json()["stats"]["overdue_count"] == 1

    async def test_workload_and_unassigned_aggregates(self, client, fake_llm):
        owner = await create_user(client)
        await create_organization(client, owner, "Acme")
        worker = await create_user(client)
        await add_member(client, owner, worker["user"]["email"])
        project_id = (await create_project(client, owner, "Beta"))["id"]
        pid = uuid.UUID(project_id)

        heavy = await create_task(client, owner, pid, "Heavy one")
        second = await create_task(client, owner, pid, "Second for worker")
        mine = await create_task(client, owner, pid, "Owner burden")
        await create_task(client, owner, pid, "Nobody owns this", priority="URGENT")

        headers = owner["_headers"]
        wid = worker["user"]["id"]
        oid = owner["user"]["id"]
        for task_obj, target in ((heavy, wid), (second, wid), (mine, oid)):
            response = await client.post(
                f"/api/v1/tasks/{task_obj['id']}/assign",
                headers=headers,
                json={"user_id": target},
            )
            assert response.status_code == 200, response.text

        fake_llm.queue(CANNED_SUMMARY)
        response = await client.get(
            f"/api/v1/ai/projects/{pid}/summary", headers=headers
        )
        stats = response.json()["stats"]

        # URGENT + unassigned → risk signal fires exactly once
        assert stats["unassigned_high_urgent_count"] == 1
        workload = {w["email"]: w["open_tasks"] for w in stats["workload_top"]}
        assert workload == {
            owner["user"]["email"]: 1,
            worker["user"]["email"]: 2,
        }

    async def test_empty_project_still_summarizes(self, client, fake_llm):
        user, pid = await self._seed_project(client)
        fake_llm.queue("## Status\n\nNo tasks yet.")

        response = await client.get(
            f"/api/v1/ai/projects/{pid}/summary", headers=user["_headers"]
        )
        assert response.status_code == 200
        stats = response.json()["stats"]
        assert stats["total_tasks"] == 0
        assert stats["progress_pct"] == 0

    async def test_cross_org_project_is_404_indistinguishable(self, client):
        outsider = await create_user(client)
        await create_organization(client, outsider, "Other Corp")
        user, pid = await self._seed_project(client)

        response = await client.get(
            f"/api/v1/ai/projects/{pid}/summary", headers=outsider["_headers"]
        )
        assert response.status_code == 404

    async def test_missing_key_is_503(self, client):
        user, pid = await self._seed_project(client)
        response = await client.get(
            f"/api/v1/ai/projects/{pid}/summary", headers=user["_headers"]
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "AI_NOT_CONFIGURED"

    async def test_provider_failure_is_502(self, client, fake_llm):
        user, pid = await self._seed_project(client)
        fake_llm.queue(httpx.ReadTimeout("boom"))
        response = await client.get(
            f"/api/v1/ai/projects/{pid}/summary", headers=user["_headers"]
        )
        assert response.status_code == 502

    async def test_empty_replies_twice_become_invalid_output(self, client, fake_llm):
        user, pid = await self._seed_project(client)
        fake_llm.queue("", "   ")
        response = await client.get(
            f"/api/v1/ai/projects/{pid}/summary", headers=user["_headers"]
        )
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "AI_INVALID_OUTPUT"
