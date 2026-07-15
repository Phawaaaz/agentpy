"""Tests for the relational store (D29): DbUserStore, DbSessionStore,
cross-user isolation, the first-account-becomes-admin bootstrap, session
delete, and the JSON->DB migration script. SQLite in a temp dir -- no key,
no network, no external database.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth.users import UserStore as JsonUserStore
from config import Config
from context_engine.compaction import Conversation
from scripts.migrate_json_to_db import migrate
from storage.db import make_engine
from storage.models import ROLE_ADMIN, ROLE_USER
from storage.session_store import DbSessionStore
from storage.user_store import DbUserStore


def _engine(tmp):
    return make_engine(f"sqlite:///{os.path.join(tmp, 'test.db')}")


def test_first_account_becomes_admin():
    with tempfile.TemporaryDirectory() as tmp:
        store = DbUserStore(_engine(tmp))
        store.register("alice", "pw1")
        store.register("bob", "pw2")
        assert store.role("alice") == ROLE_ADMIN, "first account must bootstrap as admin"
        assert store.role("bob") == ROLE_USER
        assert store.role("nobody") is None
    print("  first account becomes admin, later ones are users OK")


def test_user_store_validation_matches_legacy_rules():
    with tempfile.TemporaryDirectory() as tmp:
        store = DbUserStore(_engine(tmp))
        store.register("carol", "pw")
        for evil in ["../alice", "/tmp/evil", "a/b", "..", "user name", "u" * 65, ""]:
            try:
                store.register(evil, "somepassword")
                raised = False
            except ValueError:
                raised = True
            assert raised, f"{evil!r} should be rejected"
        try:
            store.register("carol", "again")
            raised = False
        except ValueError:
            raised = True
        assert raised, "duplicate username must raise"
        try:
            store.register("dave", "")
            raised = False
        except ValueError:
            raised = True
        assert raised, "empty password must raise"
    print("  DbUserStore keeps the legacy validation rules OK")


def test_sessions_are_isolated_per_user():
    with tempfile.TemporaryDirectory() as tmp:
        engine = _engine(tmp)
        users = DbUserStore(engine)
        alice_id = users.register("alice", "pw1")
        bob_id = users.register("bob", "pw2")

        alice_store = DbSessionStore(engine, alice_id)
        bob_store = DbSessionStore(engine, bob_id)

        conv = Conversation("SYS")
        conv.add({"role": "user", "content": "alice's secret plan"})
        alice_store.save("shared-id", conv)

        # Bob can't list, load, or delete Alice's session -- even with the
        # exact same session id.
        assert bob_store.list_ids() == []
        probe = Conversation("X")
        assert bob_store.load("shared-id", probe) is False
        assert bob_store.delete("shared-id") is False
        assert alice_store.list_ids() == ["shared-id"]
    print("  sessions are isolated per user (same id, no cross-access) OK")


def test_session_delete():
    with tempfile.TemporaryDirectory() as tmp:
        engine = _engine(tmp)
        user_id = DbUserStore(engine).register("erin", "pw")
        store = DbSessionStore(engine, user_id)
        store.save("s1", Conversation("SYS"))
        assert store.list_ids() == ["s1"]
        assert store.delete("s1") is True
        assert store.list_ids() == []
        assert store.delete("s1") is False, "second delete reports not-found"
    print("  session delete OK")


def test_migration_from_json_files():
    with tempfile.TemporaryDirectory() as tmp:
        # Build a legacy layout: users.json + per-user session dirs (D22).
        users_path = os.path.join(tmp, "users.json")
        legacy_users = JsonUserStore(users_path)
        legacy_users.register("alice", "alicepass")
        legacy_users.register("bob", "bobpass")

        sessions_dir = os.path.join(tmp, "sessions")
        conv = Conversation("SYS")
        conv.add({"role": "user", "content": "legacy history"})
        os.makedirs(os.path.join(sessions_dir, "alice"), exist_ok=True)
        with open(os.path.join(sessions_dir, "alice", "old-session.json"), "w", encoding="utf-8") as f:
            json.dump(conv.snapshot(), f)

        config = Config(
            db_url=f"sqlite:///{os.path.join(tmp, 'migrated.db')}",
            users_config_path=users_path,
            sessions_dir=sessions_dir,
        )
        counts = migrate(config)
        assert counts == {"users": 2, "sessions": 1}, counts

        # Passwords still verify against the copied hashes.
        engine = make_engine(config.db_url)
        store = DbUserStore(engine)
        assert store.verify("alice", "alicepass")
        assert store.verify("bob", "bobpass")
        assert not store.verify("alice", "wrong")

        # The migrated session loads with its history intact.
        alice_id = store.user_id("alice")
        restored = Conversation("X")
        assert DbSessionStore(engine, alice_id).load("old-session", restored)
        assert restored.messages == [{"role": "user", "content": "legacy history"}]

        # Idempotent: a second run migrates nothing new.
        assert migrate(config) == {"users": 0, "sessions": 0}
    print("  JSON->DB migration (passwords verify, history intact, idempotent) OK")


def main():
    test_first_account_becomes_admin()
    test_user_store_validation_matches_legacy_rules()
    test_sessions_are_isolated_per_user()
    test_session_delete()
    test_migration_from_json_files()
    print("STORAGE TESTS PASSED")


if __name__ == "__main__":
    main()
