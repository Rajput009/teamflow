"""Authentication flow tests — the test matrix from 04-authentication.md."""
import uuid

import jwt as pyjwt
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RefreshToken, User
from app.tests.factories import PASSWORD, auth_header, create_user, login


class TestRegistration:
    async def test_register_happy_path(self, client: AsyncClient, db_session: AsyncSession):
        body = await create_user(client, email="ahmed@test.com")

        assert body["user"]["email"] == "ahmed@test.com"
        assert body["token_type"] == "bearer"
        # tokens exist and differ
        assert body["access_token"] != body["refresh_token"]

        stored = await db_session.scalar(select(User).where(User.email == "ahmed@test.com"))
        assert stored is not None
        assert stored.hashed_password.startswith("$argon2")
        assert PASSWORD not in stored.hashed_password

    async def test_register_duplicate_email_conflict(self, client: AsyncClient):
        await create_user(client, email="dupe@test.com")
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "dupe@test.com", "password": PASSWORD, "full_name": "X"},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "EMAIL_ALREADY_EXISTS"

    async def test_register_invalid_payload(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "not-an-email", "password": "short", "full_name": ""},
        )
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "VALIDATION_ERROR"
        fields = {d["field"] for d in error["details"]}
        assert {"email", "password", "full_name"} <= fields

    async def test_email_stored_lowercase(self, client: AsyncClient):
        body = await create_user(client, email="MiXeD@Case.COM")
        assert body["user"]["email"] == "mixed@case.com"


class TestLogin:
    async def test_login_success_returns_pair(self, client: AsyncClient):
        await create_user(client, email="login@test.com")
        body = await login(client, "login@test.com")
        assert body["access_token"] and body["refresh_token"]

    async def test_wrong_password_and_unknown_email_are_indistinguishable(
        self, client: AsyncClient
    ):
        await create_user(client, email="known@test.com")

        wrong_password = await client.post(
            "/api/v1/auth/login",
            json={"email": "known@test.com", "password": "wrong-password-1"},
        )
        unknown_email = await client.post(
            "/api/v1/auth/login",
            json={"email": "ghost@test.com", "password": "whatever-pass-1"},
        )
        assert wrong_password.status_code == 401
        assert unknown_email.status_code == 401
        # identical envelope shape AND code — no enumeration oracle
        assert (
            wrong_password.json()["error"]["code"]
            == unknown_email.json()["error"]["code"]
            == "INVALID_CREDENTIALS"
        )

    async def test_login_case_insensitive_email(self, client: AsyncClient):
        await create_user(client, email="casing@test.com")
        body = await login(client, "CASING@TEST.COM")
        assert body["access_token"]


class TestProtectedRoutes:
    async def test_me_without_token(self, client: AsyncClient):
        response = await client.get("/api/v1/auth/me")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "NOT_AUTHENTICATED"

    async def test_me_with_tampered_token(self, client: AsyncClient):
        user = await create_user(client)
        tampered = user["access_token"][:-4] + "xxxx"
        response = await client.get("/api/v1/auth/me", headers=auth_header(tampered))
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INVALID_TOKEN"

    async def test_refresh_token_cannot_act_as_access_token(self, client: AsyncClient):
        user = await create_user(client)
        response = await client.get(
            "/api/v1/auth/me", headers=auth_header(user["refresh_token"])
        )
        assert response.status_code == 401

    async def test_deactivated_user_blocked(self, client: AsyncClient, db_session: AsyncSession):
        user = await create_user(client, email="fired@test.com")
        stored = await db_session.scalar(select(User).where(User.email == "fired@test.com"))
        stored.is_active = False
        await db_session.flush()

        response = await client.get("/api/v1/auth/me", headers=user["_headers"])
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "ACCOUNT_DISABLED"


class TestRefreshLifecycle:
    async def test_refresh_rotates_and_revokes_old(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user = await create_user(client)

        response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": user["refresh_token"]}
        )
        assert response.status_code == 200
        new_pair = response.json()
        assert new_pair["refresh_token"] != user["refresh_token"]

        rows = (await db_session.scalars(select(RefreshToken))).all()
        revoked = [r for r in rows if r.token_hash is not None and r.revoked_at is not None]
        active = [r for r in rows if r.revoked_at is None]
        assert len(revoked) == 1  # the original token was rotated out
        assert len(active) == 1

    async def test_reuse_of_rotated_token_revokes_everything(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user = await create_user(client)

        first = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": user["refresh_token"]}
        )
        second_gen = first.json()["refresh_token"]

        replay = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": user["refresh_token"]}
        )
        assert replay.status_code == 401

        # theft response: even the second-generation token must now be dead
        after = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": second_gen}
        )
        assert after.status_code == 401

        rows = (await db_session.scalars(select(RefreshToken))).all()
        assert all(r.revoked_at is not None for r in rows)

    async def test_logout_is_idempotent(self, client: AsyncClient):
        user = await create_user(client)
        first = await client.post(
            "/api/v1/auth/logout", json={"refresh_token": user["refresh_token"]}
        )
        again = await client.post(
            "/api/v1/auth/logout", json={"refresh_token": user["refresh_token"]}
        )
        garbage = await client.post(
            "/api/v1/auth/logout", json={"refresh_token": "nonsense"}
        )
        assert first.status_code == 204
        assert again.status_code == 204
        assert garbage.status_code == 204


class TestAccessTokenInternals:
    async def test_expired_access_token_rejected(self, client: AsyncClient):
        import os

        expired = pyjwt.encode(
            {"sub": str(uuid.uuid4()), "type": "access", "exp": 1_000_000_000},
            os.environ["JWT_SECRET_KEY"],
            algorithm="HS256",
        )
        response = await client.get("/api/v1/auth/me", headers=auth_header(expired))
        assert response.status_code == 401

    async def test_garbage_bearer_rejected(self, client: AsyncClient):
        response = await client.get("/api/v1/auth/me", headers=auth_header("garbage"))
        assert response.status_code == 401
