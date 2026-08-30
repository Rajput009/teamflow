"""Unit tests for security helpers — no DB, no HTTP, pure logic."""
import os

import jwt as pyjwt
import pytest

from app.core.exceptions import ForbiddenError
from app.core.security import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models import Membership, OrgRole
from app.services.permissions import is_manager_or_above, require_role


class TestPasswordHashing:
    def test_hash_and_verify_roundtrip(self):
        hashed = hash_password("s3cret-password")
        assert hashed.startswith("$argon2")
        assert verify_password("s3cret-password", hashed)
        assert not verify_password("wrong", hashed)

    def test_same_password_different_hashes(self):
        # salted: identical inputs must never produce identical hashes
        a = hash_password("same-input")
        b = hash_password("same-input")
        assert a != b

    def test_verify_invalid_hash_returns_false_not_crash(self):
        # A corrupted stored hash must fail the login (False), never 500.
        assert verify_password("x", "not-a-hash") is False


class TestJwt:
    def test_roundtrip(self):
        token = create_access_token("user-123")
        payload = decode_access_token(token)
        assert payload["sub"] == "user-123"
        assert payload["type"] == "access"

    def test_expired_token_rejected(self):
        import time

        expired = pyjwt.encode(
            {"sub": "u1", "type": "access", "exp": int(time.time()) - 10},
            os.environ["JWT_SECRET_KEY"],
            algorithm="HS256",
        )
        with pytest.raises(pyjwt.PyJWTError):
            decode_access_token(expired)

    def test_wrong_signature_rejected(self):
        forged = pyjwt.encode(
            {"sub": "u1", "type": "access"},
            "attacker-key",
            algorithm="HS256",
        )
        with pytest.raises(pyjwt.PyJWTError):
            decode_access_token(forged)


class TestRefreshTokenGeneration:
    def test_raw_hash_expiry_consistent(self):
        raw, token_hash, expires_at = generate_refresh_token()
        assert hash_refresh_token(raw) == token_hash
        assert len(raw) >= 32

    def test_tokens_are_unique(self):
        a = generate_refresh_token()
        b = generate_refresh_token()
        assert a[0] != b[0]
        assert a[1] != b[1]


class TestPermissionGuards:
    @staticmethod
    def _membership(role: OrgRole) -> Membership:
        return Membership(organization_id=None, user_id=None, role=role)

    def test_require_role_passes_for_allowed(self):
        require_role(self._membership(OrgRole.MANAGER), {OrgRole.MANAGER, OrgRole.ADMIN})

    def test_require_role_raises_forbidden(self):
        with pytest.raises(ForbiddenError):
            require_role(self._membership(OrgRole.MEMBER), {OrgRole.ADMIN})

    def test_hierarchy_helpers(self):
        assert is_manager_or_above(self._membership(OrgRole.MEMBER)) is False
        assert is_manager_or_above(self._membership(OrgRole.MANAGER)) is True
        assert is_manager_or_above(self._membership(OrgRole.OWNER)) is True
