import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import get_settings

_password_hasher = PasswordHasher()

REFRESH_TOKEN_BYTES = 48

# Pinned deliberately: decode() must never accept an algorithm chosen by
# configuration (or by an attacker-crafted token header).
JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    """Argon2id hash. Salted automatically; output is self-describing
    (algorithm + params embedded) so future parameter upgrades still verify
    old hashes."""
    return _password_hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _password_hasher.verify(hashed, password)
    except VerifyMismatchError:
        return False
    except InvalidHashError:
        # Corrupted/malformed stored hash: treat as failed login rather than
        # crashing — but the data corruption itself should be investigated.
        return False


def create_access_token(user_id: str | Any) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_expire_minutes)).timestamp()),
        "type": "access",
    }
    secret = settings.jwt_secret_key.get_secret_value()
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Raises jwt.PyJWTError subclasses on any problem (expired, bad signature,
    malformed). Callers decide which HTTP error that maps to."""
    settings = get_settings()
    return jwt.decode(
        token,
        settings.jwt_secret_key.get_secret_value(),
        algorithms=[JWT_ALGORITHM],
    )


def generate_refresh_token() -> tuple[str, str, datetime]:
    """Create a refresh token triple: (raw, sha256_hash, expires_at).

    The raw value goes to the client exactly once. Only the hash is stored.
    """
    raw = secrets.token_urlsafe(REFRESH_TOKEN_BYTES)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expires_at = datetime.now(UTC) + timedelta(days=get_settings().refresh_token_expire_days)
    return raw, token_hash, expires_at


def hash_refresh_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()
