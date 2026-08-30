from app.tests.factories import create_user
from app.workers.tasks import create_notification


async def test_duplicate_idempotency_key_yields_one_notification(
    client, inline_worker_jobs
):
    user = await create_user(client, email="dup@acme.com")
    me = await client.get("/api/v1/auth/me", headers=user["_headers"])
    uid = me.json()["id"]

    key = "assign:task-xyz:user-xyz"
    create_notification(uid, "TASK_ASSIGNED", {"task_title": "t", "actor_name": "a"}, key)
    create_notification(uid, "TASK_ASSIGNED", {"task_title": "t", "actor_name": "a"}, key)

    resp = await client.get("/api/v1/notifications", headers=user["_headers"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1


async def test_distinct_keys_yield_distinct_notifications(
    client, inline_worker_jobs
):
    user = await create_user(client, email="dup2@acme.com")
    me = await client.get("/api/v1/auth/me", headers=user["_headers"])
    uid = me.json()["id"]

    create_notification(uid, "TASK_ASSIGNED", {"x": 1}, "k1")
    create_notification(uid, "TASK_ASSIGNED", {"x": 1}, "k2")

    resp = await client.get("/api/v1/notifications", headers=user["_headers"])
    assert resp.json()["total"] == 2
