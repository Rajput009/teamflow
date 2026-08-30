"""AI V3 Project Chat tests (docs/wayfinder/tickets/WF-1-...).

Core property: the answer is grounded in DB-computed context the LLM is given
— never in cross-org data. Tests seed known data and assert the model
receives exactly this project's facts.
"""
import uuid

import httpx

from app.tests.factories import (
    create_organization,
    create_project,
    create_task,
    create_user,
)

CANNED_ANSWER = "The 'Late work' task is in progress and unassigned."


class TestProjectChat:
    async def _seed_project(self, client, name: str = "Alpha"):
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project = await create_project(client, user, name)
        return user, uuid.UUID(project["id"])

    async def test_chat_returns_grounded_answer(self, client, fake_llm):
        user, pid = await self._seed_project(client)
        await create_task(client, user, pid, "Late work", status="IN_PROGRESS")
        fake_llm.queue(CANNED_ANSWER)

        response = await client.post(
            f"/api/v1/ai/projects/{pid}/chat",
            headers=user["_headers"],
            json={"question": "What tasks do we have?"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["answer"] == CANNED_ANSWER
        assert body["model"]  # non-empty model name

        # grounding: the model received THIS project's facts only
        sent_context = fake_llm.calls[0][1]
        assert "Late work" in sent_context
        assert "Alpha" in sent_context

    async def test_cross_org_project_is_404(self, client, fake_llm):
        outsider = await create_user(client)
        await create_organization(client, outsider, "Other Corp")
        user, pid = await self._seed_project(client)

        response = await client.post(
            f"/api/v1/ai/projects/{pid}/chat",
            headers=outsider["_headers"],
            json={"question": "hi"},
        )
        assert response.status_code == 404

    async def test_missing_key_is_503(self, client):
        user, pid = await self._seed_project(client)
        response = await client.post(
            f"/api/v1/ai/projects/{pid}/chat",
            headers=user["_headers"],
            json={"question": "hi"},
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "AI_NOT_CONFIGURED"

    async def test_provider_failure_is_502(self, client, fake_llm):
        user, pid = await self._seed_project(client)
        fake_llm.queue(httpx.ReadTimeout("boom"))
        response = await client.post(
            f"/api/v1/ai/projects/{pid}/chat",
            headers=user["_headers"],
            json={"question": "hi"},
        )
        assert response.status_code == 502

    async def test_empty_replies_twice_become_invalid_output(self, client, fake_llm):
        user, pid = await self._seed_project(client)
        fake_llm.queue("", "   ")
        response = await client.post(
            f"/api/v1/ai/projects/{pid}/chat",
            headers=user["_headers"],
            json={"question": "hi"},
        )
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "AI_INVALID_OUTPUT"

    async def test_history_is_forwarded_to_model(self, client, fake_llm):
        user, pid = await self._seed_project(client)
        fake_llm.queue("ok")
        response = await client.post(
            f"/api/v1/ai/projects/{pid}/chat",
            headers=user["_headers"],
            json={
                "question": "follow up?",
                "history": [{"role": "user", "content": "earlier question"}],
            },
        )
        assert response.status_code == 200
        sent_context = fake_llm.calls[0][1]
        assert "earlier question" in sent_context
