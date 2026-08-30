"""AI V4 Risk Detection tests (docs/wayfinder/tickets/WF-2-...).

Core property: risks are COMPUTED from repository data (severity is
deterministic, never set by the LLM); the model only narrates and recommends
over the exact signal list it is given.
"""
import json
import uuid

from sqlalchemy import text, update

from app.models import Task
from app.tests.factories import (
    create_organization,
    create_project,
    create_task,
    create_user,
)


def _risk_json(narrative: str, kinds: list[str]) -> str:
    return json.dumps(
        {
            "narrative": narrative,
            "recommendations": [{"kind": k, "recommendation": f"fix {k}"} for k in kinds],
        }
    )


class TestProjectRisk:
    async def _seed(self, client, name: str = "Alpha"):
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project = await create_project(client, user, name)
        return user, uuid.UUID(project["id"])

    async def test_risks_are_computed_and_grounded(self, client, fake_llm, db_session):
        user, pid = await self._seed(client)
        late = await create_task(client, user, pid, "Late", priority="HIGH")
        # backdate through SQL: input validators rightly refuse past dates
        yesterday = (
            await db_session.execute(text("SELECT now()::date - INTERVAL '3 day'"))
        ).scalar()
        await db_session.execute(
            update(Task)
            .where(Task.id == uuid.UUID(late["id"]))
            .values(due_date=yesterday)
            .execution_options(synchronize_session=False)
        )
        await create_task(client, user, pid, "Urgent unassigned", priority="URGENT")
        fake_llm.queue(_risk_json("Several risks.", ["overdue_tasks", "unassigned_high_urgent"]))

        response = await client.get(
            f"/api/v1/ai/projects/{pid}/risks", headers=user["_headers"]
        )
        assert response.status_code == 200, response.text
        body = response.json()

        kinds = {r["kind"] for r in body["risks"]}
        assert "overdue_tasks" in kinds
        assert "unassigned_high_urgent" in kinds
        # severity is computed, not from the LLM
        overdue = next(r for r in body["risks"] if r["kind"] == "overdue_tasks")
        assert overdue["severity"] in ("medium", "high")
        assert overdue["recommendation"] == "fix overdue_tasks"
        assert body["narrative"] == "Several risks."

        # grounding: the model received the computed signals, nothing else
        sent = fake_llm.calls[0][1]
        assert "overdue_tasks" in sent

    async def test_no_risks_still_narrates(self, client, fake_llm):
        user, pid = await self._seed(client)
        fake_llm.queue(_risk_json("No risks detected.", []))
        response = await client.get(
            f"/api/v1/ai/projects/{pid}/risks", headers=user["_headers"]
        )
        assert response.status_code == 200
        assert response.json()["risks"] == []

    async def test_cross_org_project_is_404(self, client, fake_llm):
        outsider = await create_user(client)
        await create_organization(client, outsider, "Other Corp")
        user, pid = await self._seed(client)
        response = await client.get(
            f"/api/v1/ai/projects/{pid}/risks", headers=outsider["_headers"]
        )
        assert response.status_code == 404

    async def test_missing_key_is_503(self, client):
        user, pid = await self._seed(client)
        response = await client.get(
            f"/api/v1/ai/projects/{pid}/risks", headers=user["_headers"]
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "AI_NOT_CONFIGURED"
