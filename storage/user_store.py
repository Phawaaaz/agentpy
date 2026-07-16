"""Database-backed user accounts (D29) -- replaces the JSON-file
auth.users.UserStore behind the same exists/register/verify/list_usernames
interface, plus what the JSON store couldn't express: a real integer
user_id (the key everything downstream will use, PLAN.md Milestone 5) and
a two-tier admin/user role.

Password hashing is unchanged -- the same PBKDF2 helpers from auth/users.py
produce and verify the hash/salt pair, so accounts migrated from
.harness/users.json verify with their existing hashes.
"""

import re

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session as OrmSession

from auth.users import hash_password, verify_password

from .models import ROLE_ADMIN, ROLE_USER, User

# Same whitelist as auth.users / Config.for_user: usernames still become
# path components (memory/logs/workspace dirs), DB or not.
_VALID_USERNAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class DbUserStore:
    """Load/verify/register users against the relational store."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def exists(self, username: str) -> bool:
        with OrmSession(self.engine) as db:
            return db.scalar(select(User.id).where(User.username == username)) is not None

    def register(self, username: str, password: str) -> int:
        """Create an account and return its user_id. The very first account
        ever created becomes the admin (local-bootstrap convention,
        PLAN.md §5.5); everyone after is a regular user."""
        if not _VALID_USERNAME.match(username):
            raise ValueError(
                f"invalid username {username!r}: must be 1-64 characters of "
                "letters, digits, underscore, or hyphen"
            )
        if not password:
            raise ValueError("password must be non-empty")
        hash_hex, salt_hex = hash_password(password)
        with OrmSession(self.engine) as db:
            if db.scalar(select(User.id).where(User.username == username)) is not None:
                raise ValueError(f"user '{username}' already exists")
            first_account = db.scalar(select(User.id).limit(1)) is None
            user = User(
                username=username,
                password_hash=hash_hex,
                salt=salt_hex,
                role=ROLE_ADMIN if first_account else ROLE_USER,
            )
            db.add(user)
            db.commit()
            return user.id

    def verify(self, username: str, password: str) -> bool:
        with OrmSession(self.engine) as db:
            user = db.scalar(select(User).where(User.username == username))
        if user is None:
            return False
        return verify_password(password, user.password_hash, user.salt)

    def user_id(self, username: str) -> int | None:
        with OrmSession(self.engine) as db:
            return db.scalar(select(User.id).where(User.username == username))

    def role(self, username: str) -> str | None:
        with OrmSession(self.engine) as db:
            return db.scalar(select(User.role).where(User.username == username))

    def list_usernames(self) -> list[str]:
        with OrmSession(self.engine) as db:
            return sorted(db.scalars(select(User.username)))

    def list_users(self) -> list[tuple[str, str]]:
        """(username, role) pairs, sorted by username -- the /users listing."""
        with OrmSession(self.engine) as db:
            return sorted(db.execute(select(User.username, User.role)).all())

    def set_role(self, username: str, role: str) -> None:
        """Promote/demote an account. Refuses to demote the last admin --
        an installation with zero admins can never manage roles again."""
        if role not in (ROLE_ADMIN, ROLE_USER):
            raise ValueError(f"role must be '{ROLE_ADMIN}' or '{ROLE_USER}', got {role!r}")
        with OrmSession(self.engine) as db:
            user = db.scalar(select(User).where(User.username == username))
            if user is None:
                raise ValueError(f"no such user '{username}'")
            if user.role == ROLE_ADMIN and role == ROLE_USER:
                admins = db.scalar(
                    select(func.count()).select_from(User).where(User.role == ROLE_ADMIN)
                )
                if (admins or 0) <= 1:
                    raise ValueError("cannot demote the last admin")
            user.role = role
            db.commit()
