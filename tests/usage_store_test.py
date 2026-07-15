"""Tests for observability/usage_store.py (D30): durable per-model-call
usage rows attributed to the right user/session/task, the admin aggregation
queries, and the CLI's admin-only gating of /usage and /users.
SQLite in a temp dir -- no key, no network.
"""

import io
import os
import sys
import tempfile
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import interfaces.cli as cli
from observability.usage_store import PersistentUsageTracker, usage_by_user, usage_for_user
from providers.base import Usage
from storage.db import make_engine
from storage.models import ROLE_ADMIN, ROLE_USER
from storage.user_store import DbUserStore


def _setup(tmp):
    engine = make_engine(f"sqlite:///{os.path.join(tmp, 'test.db')}")
    users = DbUserStore(engine)
    admin_id = users.register("admin-ann", "pw")  # first account -> admin
    user_id = users.register("busy-bee", "pw")
    return engine, users, admin_id, user_id


def test_records_rows_with_current_session_and_task():
    with tempfile.TemporaryDirectory() as tmp:
        engine, _, _, user_id = _setup(tmp)
        state = {"session": "sess-A", "task": "first task"}
        tracker = PersistentUsageTracker(
            engine, user_id,
            session_id_fn=lambda: state["session"],
            task_fn=lambda: state["task"],
        )
        tracker.record("anthropic/claude-opus-4-8", Usage(1000, 500))
        state.update(session="sess-B", task="second task")
        tracker.record("anthropic/claude-opus-4-8", Usage(200, 100))

        # In-memory totals still power /cost exactly as before.
        assert tracker.calls == 2 and tracker.prompt_tokens == 1200

        rows = usage_for_user(engine, "busy-bee")
        by_session = {r["session_id"]: r for r in rows}
        assert by_session["sess-A"]["prompt_tokens"] == 1000
        assert by_session["sess-A"]["last_task"] == "first task"
        assert by_session["sess-B"]["last_task"] == "second task"
    print("  rows attributed to the session/task current at call time OK")


def test_admin_overview_aggregates_per_user():
    with tempfile.TemporaryDirectory() as tmp:
        engine, _, admin_id, user_id = _setup(tmp)
        for uid, n in ((admin_id, 1), (user_id, 3)):
            t = PersistentUsageTracker(engine, uid, lambda: "s", lambda: "t")
            for _ in range(n):
                t.record("anthropic/claude-opus-4-8", Usage(100, 50))
        rows = usage_by_user(engine)
        by_name = {r["username"]: r for r in rows}
        assert by_name["busy-bee"]["calls"] == 3
        assert by_name["admin-ann"]["calls"] == 1
        assert by_name["busy-bee"]["cost_usd"] > by_name["admin-ann"]["cost_usd"]
        assert rows[0]["username"] == "busy-bee", "sorted biggest spender first"
    print("  per-user admin aggregation OK")


def test_db_failure_never_breaks_a_run():
    with tempfile.TemporaryDirectory() as tmp:
        engine, _, _, user_id = _setup(tmp)
        tracker = PersistentUsageTracker(
            engine, user_id,
            session_id_fn=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            task_fn=lambda: "t",
        )
        tracker.record("anthropic/claude-opus-4-8", Usage(10, 5))  # must not raise
        assert tracker.calls == 1, "in-memory totals must still update"
    print("  a failing insert is swallowed; in-memory totals survive OK")


class _FakeSession:
    def __init__(self, role):
        self.role = role
        self.username = "whoever"
        self.id = "current"


def _capture(fn, *args) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*args)
    return buf.getvalue()


def test_cli_commands_are_admin_gated():
    with tempfile.TemporaryDirectory() as tmp:
        engine, users, _, user_id = _setup(tmp)
        PersistentUsageTracker(engine, user_id, lambda: "s", lambda: "t").record(
            "anthropic/claude-opus-4-8", Usage(100, 50)
        )
        # A non-admin is refused both commands.
        out = _capture(cli._handle_usage_command, [], _FakeSession(ROLE_USER), engine)
        assert "only an admin" in out, out
        out = _capture(cli._handle_users_command, [], _FakeSession(ROLE_USER), users)
        assert "only an admin" in out, out
        # An admin sees the aggregation and the account list.
        out = _capture(cli._handle_usage_command, [], _FakeSession(ROLE_ADMIN), engine)
        assert "busy-bee" in out and "tokens" in out, out
        out = _capture(cli._handle_users_command, [], _FakeSession(ROLE_ADMIN), users)
        assert "admin-ann: admin" in out and "busy-bee: user" in out, out
        # Promote/demote works, and the last admin is protected.
        out = _capture(cli._handle_users_command, ["role", "busy-bee", "admin"], _FakeSession(ROLE_ADMIN), users)
        assert "busy-bee is now admin" in out, out
        users.set_role("busy-bee", "user")
        out = _capture(cli._handle_users_command, ["role", "admin-ann", "user"], _FakeSession(ROLE_ADMIN), users)
        assert "cannot demote the last admin" in out, out
    print("  /usage and /users are admin-gated; last admin protected OK")


def main():
    test_records_rows_with_current_session_and_task()
    test_admin_overview_aggregates_per_user()
    test_db_failure_never_breaks_a_run()
    test_cli_commands_are_admin_gated()
    print("USAGE STORE TESTS PASSED")


if __name__ == "__main__":
    main()
