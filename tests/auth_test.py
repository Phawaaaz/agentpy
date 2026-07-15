"""Multi-user login tests: auth.users' password hashing/UserStore, Config's
per-user directory namespacing, and interfaces.cli's login flow (both the
HARNESS_USER/HARNESS_PASSWORD shortcut and the interactive prompt, with
input()/getpass.getpass() monkeypatched -- no real terminal needed).

No key, no network.
"""

import builtins
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import interfaces.cli as cli
from auth.users import UserStore, hash_password, verify_password
from config import Config
from storage.db import make_engine
from storage.user_store import DbUserStore


def _db_store(tmp: str) -> DbUserStore:
    return DbUserStore(make_engine(f"sqlite:///{os.path.join(tmp, 'test.db')}"))


def test_hash_password_uses_a_random_salt_and_verifies():
    hash_a, salt_a = hash_password("correct horse")
    hash_b, salt_b = hash_password("correct horse")
    assert salt_a != salt_b, "two calls should get independent random salts"
    assert hash_a != hash_b, "same password + different salt -> different hash"
    assert verify_password("correct horse", hash_a, salt_a)
    assert not verify_password("wrong password", hash_a, salt_a)
    print("  hash_password/verify_password OK")


def test_user_store_register_and_verify():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "users.json")
        store = UserStore(path)

        assert not store.exists("alice")
        store.register("alice", "hunter2")
        assert store.exists("alice")
        assert store.verify("alice", "hunter2")
        assert not store.verify("alice", "wrong")
        assert not store.verify("nobody", "anything")
        assert store.list_usernames() == ["alice"]
        print("  UserStore register/verify OK")


def test_user_store_rejects_duplicate_and_empty():
    with tempfile.TemporaryDirectory() as tmp:
        store = UserStore(os.path.join(tmp, "users.json"))
        store.register("bob", "s3cret")
        try:
            store.register("bob", "different")
            raised = False
        except ValueError:
            raised = True
        assert raised, "registering an existing username must raise"

        for username, password in [("", "x"), ("nobody", "")]:
            try:
                store.register(username, password)
                raised = False
            except ValueError:
                raised = True
            assert raised, "empty username/password must raise"
        print("  UserStore rejects duplicate/empty accounts OK")


def test_user_store_rejects_path_traversal_usernames():
    with tempfile.TemporaryDirectory() as tmp:
        store = UserStore(os.path.join(tmp, "users.json"))
        for evil in ["../alice", "/tmp/evil", "a/b", "..", "user name", "u" * 65]:
            try:
                store.register(evil, "somepassword")
                raised = False
            except ValueError:
                raised = True
            assert raised, f"{evil!r} should be rejected as an unsafe username"
        # a normal username still works
        store.register("alice-2", "somepassword")
        assert store.exists("alice-2")
        print("  UserStore rejects path-traversal-shaped usernames OK")


def test_password_never_stored_in_plaintext():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "users.json")
        store = UserStore(path)
        store.register("carol", "swordfish-secret")
        with open(path) as f:
            raw = f.read()
        assert "swordfish-secret" not in raw
        print("  password never stored in plaintext OK")


def test_config_for_user_namespaces_only_per_user_dirs():
    base = Config(
        model="anthropic/claude-opus-4-8",
        sessions_dir=".harness/sessions",
        memory_dir=".harness/memory",
        logs_dir=".harness/logs",
        offload_dir=".harness/offload",
        mcp_config_path=".harness/mcp.json",
    )
    scoped = base.for_user("dave")

    assert scoped.sessions_dir == os.path.join(".harness/sessions", "dave")
    assert scoped.memory_dir == os.path.join(".harness/memory", "dave")
    assert scoped.logs_dir == os.path.join(".harness/logs", "dave")
    assert scoped.offload_dir == os.path.join(".harness/offload", "dave")
    # Org-wide config is untouched -- it isn't a user's data.
    assert scoped.model == base.model
    assert scoped.mcp_config_path == base.mcp_config_path
    # Original Config is untouched (for_user returns a copy).
    assert base.sessions_dir == ".harness/sessions"
    print("  Config.for_user namespaces only per-user dirs OK")


def test_config_for_user_rejects_path_traversal_usernames():
    base = Config(sessions_dir=".harness/sessions", memory_dir=".harness/memory")
    for evil in ["../alice", "/tmp/evil", "a/b", ".."]:
        try:
            base.for_user(evil)
            raised = False
        except ValueError:
            raised = True
        assert raised, f"{evil!r} should be rejected -- it must not escape .harness/"
    print("  Config.for_user rejects path-traversal-shaped usernames (defense in depth) OK")


def test_login_env_var_shortcut_existing_user():
    with tempfile.TemporaryDirectory() as tmp:
        store = _db_store(tmp)
        store.register("erin", "topsecret")
        os.environ["HARNESS_USER"] = "erin"
        os.environ["HARNESS_PASSWORD"] = "topsecret"
        try:
            username = cli._login(store)
        finally:
            del os.environ["HARNESS_USER"]
            del os.environ["HARNESS_PASSWORD"]
        assert username == "erin"
        print("  HARNESS_USER/HARNESS_PASSWORD shortcut (existing user) OK")


def test_login_env_var_shortcut_registers_new_user():
    with tempfile.TemporaryDirectory() as tmp:
        store = _db_store(tmp)
        os.environ["HARNESS_USER"] = "frank"
        os.environ["HARNESS_PASSWORD"] = "brandnew"
        try:
            username = cli._login(store)
        finally:
            del os.environ["HARNESS_USER"]
            del os.environ["HARNESS_PASSWORD"]
        assert username == "frank"
        assert store.verify("frank", "brandnew")
        print("  HARNESS_USER/HARNESS_PASSWORD shortcut (new user) OK")


def test_login_env_var_shortcut_wrong_password_exits():
    with tempfile.TemporaryDirectory() as tmp:
        store = _db_store(tmp)
        store.register("grace", "realpassword")
        os.environ["HARNESS_USER"] = "grace"
        os.environ["HARNESS_PASSWORD"] = "wrongpassword"
        try:
            try:
                cli._login(store)
                raised = False
            except SystemExit:
                raised = True
        finally:
            del os.environ["HARNESS_USER"]
            del os.environ["HARNESS_PASSWORD"]
        assert raised
        print("  wrong HARNESS_PASSWORD exits instead of logging in OK")


def test_login_interactive_registers_and_signs_in():
    with tempfile.TemporaryDirectory() as tmp:
        store = _db_store(tmp)
        inputs = iter(["heidi"])
        passwords = iter(["newpass123", "newpass123"])  # choose + confirm

        original_input, original_getpass = builtins.input, cli.getpass.getpass
        builtins.input = lambda *_a, **_k: next(inputs)
        cli.getpass.getpass = lambda *_a, **_k: next(passwords)
        try:
            username = cli._login(store)
        finally:
            builtins.input = original_input
            cli.getpass.getpass = original_getpass

        assert username == "heidi"
        assert store.verify("heidi", "newpass123")
        print("  interactive login registers + signs in a new user OK")


def test_login_interactive_existing_user_wrong_then_right_password():
    with tempfile.TemporaryDirectory() as tmp:
        store = _db_store(tmp)
        store.register("ivan", "rightpass")
        inputs = iter(["ivan", "ivan"])  # re-prompted for username after a wrong password
        passwords = iter(["wrongpass", "rightpass"])

        original_input, original_getpass = builtins.input, cli.getpass.getpass
        builtins.input = lambda *_a, **_k: next(inputs)
        cli.getpass.getpass = lambda *_a, **_k: next(passwords)
        try:
            username = cli._login(store)
        finally:
            builtins.input = original_input
            cli.getpass.getpass = original_getpass

        assert username == "ivan"
        print("  interactive login retries after a wrong password OK")


def main():
    test_hash_password_uses_a_random_salt_and_verifies()
    test_user_store_register_and_verify()
    test_user_store_rejects_duplicate_and_empty()
    test_user_store_rejects_path_traversal_usernames()
    test_password_never_stored_in_plaintext()
    test_config_for_user_namespaces_only_per_user_dirs()
    test_config_for_user_rejects_path_traversal_usernames()
    test_login_env_var_shortcut_existing_user()
    test_login_env_var_shortcut_registers_new_user()
    test_login_env_var_shortcut_wrong_password_exits()
    test_login_interactive_registers_and_signs_in()
    test_login_interactive_existing_user_wrong_then_right_password()
    print("AUTH TESTS PASSED")


if __name__ == "__main__":
    main()
