"""Password hashing, plus the legacy JSON-file account store.

Passwords are never stored in plaintext: PBKDF2-HMAC-SHA256 with a random
per-user salt, stdlib only (`hashlib` + `secrets`), no extra dependency.
`hash_password`/`verify_password` are the live hashing implementation --
storage/user_store.py's database-backed accounts use them (D29).

`UserStore` (the JSON file at .harness/users.json) is LEGACY as of D29: the
CLI now authenticates against storage/user_store.py's DbUserStore. The class
stays only as the read-side of scripts/migrate_json_to_db.py and for any
older checkout's data; new code should not write accounts through it.
"""

import hashlib
import json
import os
import re
import secrets

_ITERATIONS = 200_000

# Usernames become directory path components elsewhere (Config.for_user), so
# they're confined to a safe charset at the point they're first created --
# "../alice" or "/tmp/evil" must never make it into the accounts file.
_VALID_USERNAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """Return (hash_hex, salt_hex). Generates a fresh salt if none is given."""
    salt = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return digest.hex(), salt.hex()


def verify_password(password: str, hash_hex: str, salt_hex: str) -> bool:
    candidate, _ = hash_password(password, bytes.fromhex(salt_hex))
    return secrets.compare_digest(candidate, hash_hex)


class UserStore:
    """Load/verify/register users against a JSON file at `path`.

    Shape: {"<username>": {"hash": "...", "salt": "..."}}. Reads and writes
    the whole file each call -- fine for the account volumes a JSON-file
    store is meant for (see D12's same trade-off for SessionStore); swap the
    backend behind this same interface if that ever stops being true.
    """

    def __init__(self, path: str):
        self.path = path

    def _load(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        with open(self.path) as f:
            return json.load(f)

    def _save(self, data: dict) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(data, f, indent=2)

    def exists(self, username: str) -> bool:
        return username in self._load()

    def register(self, username: str, password: str) -> None:
        if not _VALID_USERNAME.match(username):
            raise ValueError(
                f"invalid username {username!r}: must be 1-64 characters of "
                "letters, digits, underscore, or hyphen"
            )
        if not password:
            raise ValueError("password must be non-empty")
        data = self._load()
        if username in data:
            raise ValueError(f"user '{username}' already exists")
        hash_hex, salt_hex = hash_password(password)
        data[username] = {"hash": hash_hex, "salt": salt_hex}
        self._save(data)

    def verify(self, username: str, password: str) -> bool:
        record = self._load().get(username)
        if record is None:
            return False
        return verify_password(password, record["hash"], record["salt"])

    def list_usernames(self) -> list[str]:
        return sorted(self._load().keys())
