"""Small helpers to build test state through the real API.

Integration-flavored factories: users/orgs/projects/tasks are created via
HTTP so every factory call also exercises the production code paths.
"""
import uuid

from httpx import AsyncClient

PASSWORD = "password123"


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def create_user(
    client: AsyncClient,
    email: str | None = None,
    password: str = PASSWORD,
    full_name: str = "Test User",
) -> dict:
    """Register a user; returns the full register response (user + tokens)."""
    email = email or f"user-{uuid.uuid4().hex[:8]}@test.com"
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": full_name},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    body["password"] = password
    body["_headers"] = auth_header(body["access_token"])
    return body


async def login(client: AsyncClient, email: str, password: str = PASSWORD) -> dict:
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    body["email"] = email
    body["password"] = password
    body["_headers"] = auth_header(body["access_token"])
    return body


async def create_organization(client: AsyncClient, user: dict, name: str) -> dict:
    """Create an org as `user`; returns the org object."""
    response = await client.post(
        "/api/v1/organizations",
        headers=user["_headers"],
        json={"name": name},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def add_member(
    client: AsyncClient, owner: dict, email: str, role: str = "MEMBER"
) -> dict:
    response = await client.post(
        "/api/v1/organizations/members",
        headers=owner["_headers"],
        json={"email": email, "role": role},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_project(client: AsyncClient, user: dict, name: str) -> dict:
    response = await client.post(
        "/api/v1/projects", headers=user["_headers"], json={"name": name}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def add_project_member(
    client: AsyncClient, manager: dict, project_id: uuid.UUID, user_id: uuid.UUID
) -> dict:
    response = await client.post(
        f"/api/v1/projects/{project_id}/members",
        headers=manager["_headers"],
        json={"user_id": str(user_id)},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_task(
    client: AsyncClient,
    user: dict,
    project_id: uuid.UUID,
    title: str,
    **overrides: object,
) -> dict:
    payload: dict = {"title": title, **overrides}
    response = await client.post(
        f"/api/v1/projects/{project_id}/tasks",
        headers=user["_headers"],
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()
