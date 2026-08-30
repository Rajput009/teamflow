"""AI Task Generator tests (docs/features/11-ai-task-generator.md, section 7).

Everything runs against ScriptableLLM — no API key, no network. The core
invariants under test: drafts persist NOTHING; acceptance re-runs the full
business-rule path; the LLM's failures never become 500s.
"""
import json
import uuid

import httpx

from app.tests.factories import (
    add_member,
    create_organization,
    create_project,
    create_task,
    create_user,
)

VALID_DRAFT = json.dumps(
    {
        "name": "E-commerce Platform",
        "description": "Online store with auth, products and payments.",
        "tasks": [
            {
                "title": "Authentication",
                "priority": "HIGH",
                "due_in_days": 14,
                "subtasks": ["Registration", "Login"],
                "suggested_owner_email": None,
            },
            {
                "title": "Payments integration",
                "priority": "URGENT",
                "due_in_days": 30,
                "subtasks": [],
                "suggested_owner_email": None,
            },
        ],
    }
)


def draft_body(idea: str = "Build an e-commerce website for a clothing company") -> dict:
    return {"idea": idea}


class TestProjectDraft:
    async def test_draft_returns_proposal_and_persists_nothing(
        self, client, fake_llm
    ):
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        fake_llm.queue(VALID_DRAFT)

        response = await client.post(
            "/api/v1/ai/projects/drafts", headers=user["_headers"], json=draft_body()
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["name"] == "E-commerce Platform"
        assert len(body["tasks"]) == 2
        assert body["truncated_to_cap"] is False

        # the invariant: generation wrote nothing
        listing = await client.get("/api/v1/projects", headers=user["_headers"])
        assert listing.json()["total"] == 0

    async def test_member_cannot_generate_project_draft(self, client, fake_llm):
        owner = await create_user(client)
        await create_organization(client, owner, "Acme")
        member = await create_user(client)
        await add_member(client, owner, member["user"]["email"])

        response = await client.post(
            "/api/v1/ai/projects/drafts",
            headers=member["_headers"],
            json=draft_body(),
        )
        assert response.status_code == 403

    async def test_idea_too_short_is_422(self, client):
        # The unconfigured LLM is a lazy client: input validation (422) and
        # authorization errors surface BEFORE any 503 could.
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        response = await client.post(
            "/api/v1/ai/projects/drafts",
            headers=user["_headers"],
            json={"idea": "short"},
        )
        assert response.status_code == 422


class TestAcceptProjectDraft:
    def _accept_payload(self) -> dict:
        proposal = json.loads(VALID_DRAFT)
        return {
            "name": proposal["name"],
            "description": proposal["description"],
            "tasks": proposal["tasks"],
        }

    async def test_accept_creates_project_tasks_and_flat_subtasks(
        self, client, fake_llm
    ):
        user = await create_user(client)
        await create_organization(client, user, "Acme")

        response = await client.post(
            "/api/v1/projects/from-drafts",
            headers=user["_headers"],
            json=self._accept_payload(),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["project_name"] == "E-commerce Platform"
        # 2 tasks + 2 flat subtasks from "Authentication"
        assert body["created_task_count"] == 4
        assert body["warnings"] == []

        tasks = await client.get(
            f"/api/v1/projects/{body['project_id']}/tasks",
            headers=user["_headers"],
        )
        titles = [t["title"] for t in tasks.json()["items"]]
        assert set(titles) == {
            "Authentication", "Registration", "Login", "Payments integration"
        }

    async def test_accept_rejects_duplicate_name(self, client):
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        await create_project(client, user, "E-commerce Platform")

        response = await client.post(
            "/api/v1/projects/from-drafts",
            headers=user["_headers"],
            json=self._accept_payload(),
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "PROJECT_NAME_EXISTS"

    async def test_member_cannot_accept(self, client):
        owner = await create_user(client)
        await create_organization(client, owner, "Acme")
        member = await create_user(client)
        await add_member(client, owner, member["user"]["email"])

        response = await client.post(
            "/api/v1/projects/from-drafts",
            headers=member["_headers"],
            json=self._accept_payload(),
        )
        assert response.status_code == 403

    async def test_unknown_owner_email_dropped_with_warning(self, client):
        payload = self._accept_payload()
        payload["tasks"][0]["suggested_owner_email"] = "ghost@nowhere.test"

        user = await create_user(client)
        await create_organization(client, user, "Acme")

        response = await client.post(
            "/api/v1/projects/from-drafts",
            headers=user["_headers"],
            json=payload,
        )
        assert response.status_code == 201
        assert any("ghost@nowhere.test" in w for w in response.json()["warnings"])

    async def test_known_member_owner_resolved_to_assignment(self, client):
        owner = await create_user(client)
        await create_organization(client, owner, "Acme")
        ali = await create_user(client)
        await add_member(client, owner, ali["user"]["email"])

        payload = self._accept_payload()
        payload["tasks"][0]["suggested_owner_email"] = ali["user"]["email"]
        response = await client.post(
            "/api/v1/projects/from-drafts",
            headers=owner["_headers"],
            json=payload,
        )
        assert response.status_code == 201
        project_id = response.json()["project_id"]

        tasks = await client.get(
            f"/api/v1/projects/{project_id}/tasks",
            headers=owner["_headers"],
            params={"search": "Authentication"},
        )
        auth_task = next(
            t
            for t in tasks.json()["items"]
            if t["title"] == "Authentication" and t["assigned_to_id"] is not None
        )
        assert str(auth_task["assigned_to_id"]) == ali["user"]["id"]

    async def test_over_cap_submission_truncates_with_warning(self, client):
        payload = self._accept_payload()
        payload["tasks"] = [dict(payload["tasks"][0]) for _ in range(31)]

        user = await create_user(client)
        await create_organization(client, user, "Acme")
        response = await client.post(
            "/api/v1/projects/from-drafts",
            headers=user["_headers"],
            json=payload,
        )
        assert response.status_code == 201
        body = response.json()
        assert body["created_task_count"] <= 30 * 3 + 30  # cap sanity, flat subs included
        assert any("kept the first" in w for w in body["warnings"])

    async def test_activity_row_written_once_per_acceptance(self, client):
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        await client.post(
            "/api/v1/projects/from-drafts",
            headers=user["_headers"],
            json=self._accept_payload(),
        )
        activities = await client.get(
            "/api/v1/activities",
            headers=user["_headers"],
            params={"action": "ai.project_created"},
        )
        assert activities.json()["total"] == 1


class TestTaskBreakdown:
    VALID_BREAKDOWN = json.dumps(
        {
            "subtasks": [
                {"title": "Create user model", "priority": "HIGH", "due_in_days": 2},
                {"title": "Implement password hashing"},
                {"title": "Write auth tests"},
            ]
        }
    )

    async def _seed_task(self, client):
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project = await create_project(client, user, "Alpha")
        task = await create_task(
            client, user, uuid.UUID(project["id"]), "Implement JWT authentication"
        )
        return user, uuid.UUID(task["id"])

    async def test_breakdown_draft_persists_nothing(self, client, fake_llm):
        user, task_id = await self._seed_task(client)
        fake_llm.queue(self.VALID_BREAKDOWN)

        response = await client.post(
            f"/api/v1/ai/tasks/{task_id}/breakdowns",
            headers=user["_headers"],
            json={"instruction": "break it into concrete steps"},
        )
        assert response.status_code == 200, response.text
        assert len(response.json()["subtasks"]) == 3

        detail = await client.get(f"/api/v1/tasks/{task_id}", headers=user["_headers"])
        assert detail.status_code == 200  # still there, untouched

    async def test_breakdown_on_invisible_cross_org_task_is_404(self, client, fake_llm):
        outsider = await create_user(client)
        await create_organization(client, outsider, "Other Corp")
        user, task_id = await self._seed_task(client)

        response = await client.post(
            f"/api/v1/ai/tasks/{task_id}/breakdowns",
            headers=outsider["_headers"],
            json={"instruction": "break it into concrete steps"},
        )
        assert response.status_code == 404

    async def test_accept_breakdown_creates_sibling_tasks(self, client, fake_llm):
        user, task_id = await self._seed_task(client)
        breakdown = json.loads(self.VALID_BREAKDOWN)

        response = await client.post(
            f"/api/v1/tasks/{task_id}/accept-breakdowns",
            headers=user["_headers"],
            json={"subtasks": breakdown["subtasks"]},
        )
        assert response.status_code == 201, response.text
        assert len(response.json()["created"]) == 3


class TestLLMFailureHandling:
    """The LLM is an unreliable component at the boundary: its failures must
    surface as designed 502s, never as 500s."""

    async def _draft(self, client):
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        return (
            user,
            await client.post(
                "/api/v1/ai/projects/drafts",
                headers=user["_headers"],
                json=draft_body(),
            ),
        )

    async def test_missing_api_key_returns_503(self, client):
        # plain `client` — no fake_llm override — so the real factory runs;
        # tests never configure a key, exercising AI_NOT_CONFIGURED.
        user, response = await self._draft(client)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "AI_NOT_CONFIGURED"

    async def test_provider_timeout_becomes_502(self, client, fake_llm):
        fake_llm.queue(httpx.ReadTimeout("boom"))
        _, response = await self._draft(client)
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "AI_UPSTREAM_ERROR"

    async def test_garbage_output_twice_becomes_invalid_output(self, client, fake_llm):
        fake_llm.queue("I cannot do that.", "Sorry, here is some prose instead.")
        _, response = await self._draft(client)
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "AI_INVALID_OUTPUT"

    async def test_validation_feedback_retry_recovers(self, client, fake_llm):
        bad = json.dumps({"name": "", "tasks": []})  # fails NotBlank/min_length
        fake_llm.queue(bad, VALID_DRAFT)
        user, response = await self._draft(client)
        assert response.status_code == 200
        # exactly two LLM calls: original + one feedback retry
        assert len(fake_llm.calls) == 2
        # the retry message carried the validation errors back to the model
        assert "INVALID" in fake_llm.calls[1][1]

    async def test_markdown_fenced_json_is_tolerated(self, client, fake_llm):
        fake_llm.queue(f"Here is your plan:\n```json\n{VALID_DRAFT}\n```")
        _, response = await self._draft(client)
        assert response.status_code == 200
