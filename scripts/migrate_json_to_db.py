"""One-time migration: JSON-file users/sessions -> the relational store (D29).

Reads the legacy stores the harness wrote before the database existed --
.harness/users.json (accounts) and .harness/sessions/<username>/*.json
(per-user conversation snapshots, D22's namespacing) -- and inserts them
into whatever HARNESS_DB_URL points at. Hashes/salts are copied verbatim
(the hashing scheme didn't change), so every existing password still works.
Idempotent: users/sessions already present in the database are skipped, so
re-running is safe.

Run from the repo root:  python scripts/migrate_json_to_db.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from config import Config
from storage.db import make_engine
from storage.models import ROLE_ADMIN, ROLE_USER, Session as SessionRow, User


def migrate(config: Config) -> dict:
    """Returns {"users": n_migrated, "sessions": n_migrated} counts."""
    engine = make_engine(config.db_url)
    counts = {"users": 0, "sessions": 0}

    accounts: dict = {}
    if os.path.exists(config.users_config_path):
        with open(config.users_config_path, encoding="utf-8") as f:
            accounts = json.load(f)

    with OrmSession(engine) as db:
        no_users_yet = db.scalar(select(User.id).limit(1)) is None
        for i, (username, record) in enumerate(sorted(accounts.items())):
            if db.scalar(select(User.id).where(User.username == username)) is not None:
                continue  # already migrated
            db.add(
                User(
                    username=username,
                    password_hash=record["hash"],
                    salt=record["salt"],
                    # Same bootstrap rule as DbUserStore.register: if the DB
                    # is empty, the first (alphabetical) migrated account
                    # becomes admin; demote/promote later via /users role.
                    role=ROLE_ADMIN if (no_users_yet and i == 0) else ROLE_USER,
                )
            )
            counts["users"] += 1
        db.commit()

        for username in sorted(accounts):
            user_id = db.scalar(select(User.id).where(User.username == username))
            user_sessions_dir = os.path.join(config.sessions_dir, username)
            if user_id is None or not os.path.isdir(user_sessions_dir):
                continue
            for name in sorted(os.listdir(user_sessions_dir)):
                if not name.endswith(".json"):
                    continue
                session_id = name[:-5]
                if db.get(SessionRow, (session_id, user_id)) is not None:
                    continue  # already migrated
                path = os.path.join(user_sessions_dir, name)
                with open(path, encoding="utf-8") as f:
                    snapshot = f.read()
                mtime = os.path.getmtime(path)
                db.add(
                    SessionRow(
                        id=session_id, user_id=user_id, snapshot_json=snapshot,
                        created_at=mtime, updated_at=mtime,
                    )
                )
                counts["sessions"] += 1
        db.commit()

    return counts


def main() -> None:
    config = Config.load()
    counts = migrate(config)
    print(f"migrated {counts['users']} user(s) and {counts['sessions']} session(s) -> {config.db_url}")
    if counts["users"] == 0 and counts["sessions"] == 0:
        print("(nothing to migrate -- no legacy JSON data found, or it was already migrated)")


if __name__ == "__main__":
    main()
