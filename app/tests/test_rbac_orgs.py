"""RBAC + organization tests — the permission matrix from 05-rbac-multi-tenancy.md."""
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.tests.factories import (
    add_member,
    create_organization,
    create_user,
)


async def _setup_org(client: AsyncClient):
    owner = await create_user(client, email="owner@test.com", full_name="Owner")
    org = await create_organization(client, owner, "Acme Software")
    return owner, org


class TestOrganizationCreation:
    async def test_creator_becomes_owner(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from sqlalchemy import select

        from app.models import Membership, OrgRole

        user = await create_user(client)
        org = await create_organization(client, user, "Test Co")

        membership = await db_session.scalar(select(Membership))
        assert membership.role == OrgRole.OWNER
        assert str(membership.organization_id) == org["id"]

    async def test_current_returns_role(self, client: AsyncClient):
        user = await create_user(client)
        await create_organization(client, user, "My Org")
        response = await client.get(
            "/api/v1/organizations/current", headers=user["_headers"]
        )
        body = response.json()
        assert body["my_role"] == "OWNER"
        assert body["organization"]["name"] == "My Org"

    async def test_user_without_org_gets_403(self, client: AsyncClient):
        loner = await create_user(client)
        response = await client.get(
            "/api/v1/organizations/current", headers=loner["_headers"]
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "NO_ORGANIZATION"


class TestMemberManagement:
    async def test_add_member_flow(self, client: AsyncClient):
        owner, _org = await _setup_org(client)
        member = await create_user(client, email="member@test.com")

        added = await add_member(client, owner, "member@test.com", role="MEMBER")
        assert added["role"] == "MEMBER"
        assert added["user_id"] == member["user"]["id"]

        # member can now see the org
        current = await client.get(
            "/api/v1/organizations/current", headers=member["_headers"]
        )
        assert current.status_code == 200

    async def test_duplicate_membership_conflict(self, client: AsyncClient):
        owner, _org = await _setup_org(client)
        await create_user(client, email="dup-member@test.com")
        await add_member(client, owner, "dup-member@test.com")
        response = await client.post(
            "/api/v1/organizations/members",
            headers=owner["_headers"],
            json={"email": "dup-member@test.com", "role": "MEMBER"},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "ALREADY_MEMBER"

    async def test_add_unregistered_email_fails(self, client: AsyncClient):
        owner, _org = await _setup_org(client)
        response = await client.post(
            "/api/v1/organizations/members",
            headers=owner["_headers"],
            json={"email": "ghost@test.com", "role": "MEMBER"},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "USER_NOT_FOUND"

    async def test_cannot_assign_owner_or_admin_directly(self, client: AsyncClient):
        owner, _org = await _setup_org(client)
        await create_user(client, email="elevated@test.com")
        for role in ("OWNER", "ADMIN"):
            response = await client.post(
                "/api/v1/organizations/members",
                headers=owner["_headers"],
                json={"email": "elevated@test.com", "role": role},
            )
            assert response.status_code == 403


class TestRoleMatrix:
    async def test_member_cannot_add_members(self, client: AsyncClient):
        owner, _org = await _setup_org(client)
        member = await create_user(client, email="m1@test.com")
        await add_member(client, owner, "m1@test.com")
        candidate = await create_user(client, email="m2@test.com")

        response = await client.post(
            "/api/v1/organizations/members",
            headers=member["_headers"],
            json={"email": candidate["user"]["email"], "role": "MEMBER"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_member_cannot_delete_organization(self, client: AsyncClient):
        owner, _org = await _setup_org(client)
        member = await create_user(client, email="destroyer@test.com")
        await add_member(client, owner, "destroyer@test.com")

        response = await client.delete(
            "/api/v1/organizations/current", headers=member["_headers"]
        )
        assert response.status_code == 403

    async def test_admin_cannot_modify_owner(self, client: AsyncClient):
        owner, _org = await _setup_org(client)
        # Admins cannot be created directly — join as MEMBER, then promote.
        admin = await create_user(client, email="admin@test.com")
        added = await add_member(client, owner, "admin@test.com", role="MEMBER")
        promote = await client.patch(
            f"/api/v1/organizations/members/{added['user_id']}",
            headers=owner["_headers"],
            json={"role": "ADMIN"},
        )
        assert promote.status_code == 200

        members = (
            await client.get("/api/v1/organizations/members", headers=owner["_headers"])
        ).json()
        owner_id = next(
            m["user_id"]
            for m in members["items"]
            if m["email"] == owner["user"]["email"]
        )

        demote = await client.patch(
            f"/api/v1/organizations/members/{owner_id}",
            headers=admin["_headers"],
            json={"role": "MEMBER"},
        )
        remove = await client.delete(
            f"/api/v1/organizations/members/{owner_id}",
            headers=admin["_headers"],
        )
        assert demote.status_code == 403
        assert remove.status_code == 403
        assert "Owners" in demote.json()["error"]["message"]

    async def test_last_owner_protected(self, client: AsyncClient):
        owner, _org = await _setup_org(client)

        members = (
            await client.get("/api/v1/organizations/members", headers=owner["_headers"])
        ).json()
        owner_id = next(
            m["user_id"]
            for m in members["items"]
            if m["email"] == owner["user"]["email"]
        )

        response = await client.patch(
            f"/api/v1/organizations/members/{owner_id}",
            headers=owner["_headers"],
            json={"role": "MEMBER"},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "LAST_OWNER"

    async def test_self_promotion_blocked(self, client: AsyncClient):
        owner, _org = await _setup_org(client)
        member = await create_user(client, email="schemer@test.com")
        added = await add_member(client, owner, "schemer@test.com")

        response = await client.patch(
            f"/api/v1/organizations/members/{added['user_id']}",
            headers=member["_headers"],
            json={"role": "ADMIN"},
        )
        assert response.status_code == 403

    async def test_owner_promotes_and_demotes(self, client: AsyncClient):
        owner, _org = await _setup_org(client)
        await create_user(client, email="rising-star@test.com")
        added = await add_member(client, owner, "rising-star@test.com")

        promote = await client.patch(
            f"/api/v1/organizations/members/{added['user_id']}",
            headers=owner["_headers"],
            json={"role": "MANAGER"},
        )
        assert promote.status_code == 200
        assert promote.json()["role"] == "MANAGER"

        demote = await client.patch(
            f"/api/v1/organizations/members/{added['user_id']}",
            headers=owner["_headers"],
            json={"role": "MEMBER"},
        )
        assert demote.status_code == 200
