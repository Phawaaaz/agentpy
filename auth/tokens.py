"""JWT issuance and verification (D30) -- the token half of the auth
scaffolding the server phase will sit on.

The CLI issues a token at login and verifies it immediately (proving the
round trip the server's per-request middleware will run); nothing in the
CLI *requires* the token yet -- one process, one login. What matters is
the shape: `sub` carries the integer user_id (the database primary key
everything downstream is keyed by), `role` carries admin/user, so a
future HTTP handler can verify a bearer token and hand the same
(user_id, role) pair to the same downstream code with zero changes there.

The signing secret comes from HARNESS_JWT_SECRET, or is generated once
and persisted (mode 0600) at .harness/jwt_secret -- never hardcoded.
"""

import os
import secrets
import time
from typing import Callable

import jwt

_ALGORITHM = "HS256"
DEFAULT_TTL_S = 7 * 24 * 3600  # generous: nothing enforces re-login yet


class TokenError(Exception):
    """An invalid, expired, or tampered token."""


def load_or_create_secret(path: str, env_var: str = "HARNESS_JWT_SECRET") -> str:
    """The signing secret: the env var if set, else a persisted random one
    (created on first use, permissions 0600)."""
    from_env = os.getenv(env_var)
    if from_env:
        return from_env
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    secret = secrets.token_hex(32)
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(secret)
    return secret


def issue_token(
    user_id: int,
    role: str,
    secret: str,
    ttl_s: int = DEFAULT_TTL_S,
    now: Callable[[], float] = time.time,
) -> str:
    """Sign a token for `user_id` with its role. `now` is injectable so
    expiry is testable without waiting."""
    issued = int(now())
    payload = {"sub": str(user_id), "role": role, "iat": issued, "exp": issued + ttl_s}
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def verify_token(
    token: str,
    secret: str,
    now: Callable[[], float] = time.time,
) -> dict:
    """Verify signature + expiry and return {"user_id": int, "role": str}.
    Raises TokenError on anything invalid -- callers treat that as
    'not logged in', never as a crash."""
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[_ALGORITHM],
            options={"verify_exp": False},  # checked against injectable `now` below
        )
    except jwt.InvalidTokenError as exc:
        raise TokenError(f"invalid token: {exc}") from None
    if int(now()) >= int(payload.get("exp", 0)):
        raise TokenError("token expired")
    try:
        return {"user_id": int(payload["sub"]), "role": str(payload["role"])}
    except (KeyError, ValueError) as exc:
        raise TokenError(f"malformed claims: {exc}") from None
