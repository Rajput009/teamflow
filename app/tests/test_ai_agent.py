import json
import uuid

from app.tests.factories import (
    create_organization,
    create_project,
    create_task,
    create_user,
)


class TestAgentPropose:
    async def _seed(self, client):
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project = await create_project(client, user, "Alpha")
        return user, uuid.UUID(project["id"])

    async def test_propose_returns_validated_actions(self, client, fake_llm):
        user, pid = await self._seed(client)
        task = await create_task(client, user, pid, "Work")
        plan = json.dumps(
            {
                "actions": [
                    {
                        "tool": "add_comment",
                        "args": {"task_id": str(task["id"]), "content": "looks blocked"},
                    }
                ]
            }
        )
        fake_llm.queue(plan)

        resp = await client.post(
            f"/api/v1/ai/projects/{pid}/agent",
            headers=user["_headers"],
            json={"instruction": "comment that it's blocked"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["actions"][0]["tool"] == "add_comment"
        assert body["actions"][0]["args"]["task_id"] == str(task["id"])

    async def test_propose_forwards_allowed_tools_to_model(self, client, fake_llm):
        user, pid = await self._seed(client)
        fake_llm.queue(json.dumps({"actions": []}))

        await client.post(
            f"/api/v1/ai/projects/{pid}/agent",
            headers=user["_headers"],
            json={"instruction": "do nothing"},
        )
        # the model message must enumerate the audited tool schemas
        sent = fake_llm.calls[-1][1]
        assert "create_task" in sent
        assert "add_comment" in sent

    async def test_unknown_tool_rejected(self, client, fake_llm):
        user, pid = await self._seed(client)
        fake_llm.queue(json.dumps({"actions": [{"tool": "delete_everything", "args": {}}]}))

        resp = await client.post(
            f"/api/v1/ai/projects/{pid}/agent",
            headers=user["_headers"],
            json={"instruction": "be evil"},
        )
        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "AI_INVALID_OUTPUT"

    async def test_propose_allows_full_toolset(self, client, fake_llm):
        user, pid = await self._seed(client)
        task = await create_task(client, user, pid, "Work")
        plan = json.dumps(
            {
                "actions": [
                    {
                        "tool": "update_task_status",
                        "args": {"task_id": str(task["id"]), "status": "IN_REVIEW"},
                    },
                    {
                        "tool": "assign_task",
                        "args": {
                            "task_id": str(task["id"]),
                            "assignee_email": "someone@acme.com",
                        },
                    },
                ]
            }
        )
        fake_llm.queue(plan)

        resp = await client.post(
            f"/api/v1/ai/projects/{pid}/agent",
            headers=user["_headers"],
            json={"instruction": "block it and assign"},
        )
        assert resp.status_code == 200, resp.text
        tools = {a["tool"] for a in resp.json()["actions"]}
        assert tools == {"update_task_status", "assign_task"}

    async def test_cross_org_404(self, client, fake_llm):
        user, pid = await self._seed(client)
        outsider = await create_user(client)
        await create_organization(client, outsider, "Other")

        resp = await client.post(
            f"/api/v1/ai/projects/{pid}/agent",
            headers=outsider["_headers"],
            json={"instruction": "review the work"},
        )
        assert resp.status_code == 404

    async def test_missing_key_503(self, client):
        user, pid = await self._seed(client)

        resp = await client.post(
            f"/api/v1/ai/projects/{pid}/agent",
            headers=user["_headers"],
            json={"instruction": "review the work"},
        )
        assert resp.status_code == 503


class TestAgentApprove:
    async def _seed(self, client):
        user = await create_user(client)
        await create_organization(client, user, "Acme")
        project = await create_project(client, user, "Alpha")
        return user, uuid.UUID(project["id"])

    async def test_approve_executes_comment(self, client, fake_llm):
        user, pid = await self._seed(client)
        task = await create_task(client, user, pid, "Work")

        # propose -> approve round trip
        fake_llm.queue(
            json.dumps(
                {
                    "actions": [
                        {
                            "tool": "add_comment",
                            "args": {
                                "task_id": str(task["id"]),
                                "content": "needs review",
                            },
                        }
                    ]
                }
            )
        )
        prop = await client.post(
            f"/api/v1/ai/projects/{pid}/agent",
            headers=user["_headers"],
            json={"instruction": "comment"},
        )
        actions = prop.json()["actions"]

        resp = await client.post(
            f"/api/v1/ai/projects/{pid}/agent/approve",
            headers=user["_headers"],
            json={"actions": actions},
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]
        assert result["ok"] is True
        assert "comment_id" in result["result"]

    async def test_approve_rejects_unknown_tool(self, client, fake_llm):
        user, pid = await self._seed(client)
        await create_task(client, user, pid, "Work")

        resp = await client.post(
            f"/api/v1/ai/projects/{pid}/agent/approve",
            headers=user["_headers"],
            json={"actions": [{"tool": "nuke", "args": {}}]},
        )
        assert resp.status_code == 502

    async def test_approve_cannot_target_other_project_task(self, client, fake_llm):
        user, pid = await self._seed(client)
        # a task in a DIFFERENT project, same org
        other = await create_project(client, user, "Beta")
        other_task = await create_task(client, user, uuid.UUID(other["id"]), "Elsewhere")

        resp = await client.post(
            f"/api/v1/ai/projects/{pid}/agent/approve",
            headers=user["_headers"],
            json={
                "actions": [
                    {
                        "tool": "add_comment",
                        "args": {
                            "task_id": str(other_task["id"]),
                            "content": "cross-project write attempt",
                        },
                    }
                ]
            },
        )
        assert resp.status_code == 502

    async def test_approve_executes_assign_and_status(self, client, fake_llm):
        user = await create_user(client, email="owner@acme.com")
        await create_organization(client, user, "Acme")
        project = await create_project(client, user, "Alpha")
        pid = uuid.UUID(project["id"])
        task = await create_task(client, user, pid, "Work")

        plan = json.dumps(
            {
                "actions": [
                    {
                        "tool": "update_task_status",
                        "args": {"task_id": str(task["id"]), "status": "IN_PROGRESS"},
                    },
                    {
                        "tool": "assign_task",
                        "args": {
                            "task_id": str(task["id"]),
                            "assignee_email": "owner@acme.com",
                        },
                    },
                ]
            }
        )
        fake_llm.queue(plan)
        prop = await client.post(
            f"/api/v1/ai/projects/{pid}/agent",
            headers=user["_headers"],
            json={"instruction": "start it and assign to me"},
        )
        actions = prop.json()["actions"]

        resp = await client.post(
            f"/api/v1/ai/projects/{pid}/agent/approve",
            headers=user["_headers"],
            json={"actions": actions},
        )
        assert resp.status_code == 200, resp.text
        results = {r["tool"]: r for r in resp.json()["results"]}
        assert results["update_task_status"]["ok"] is True
        assert results["update_task_status"]["result"]["status"] == "IN_PROGRESS"
        assert results["assign_task"]["ok"] is True
