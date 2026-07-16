"""Regression tests for the verification-review fixes:

- pipeline_cli fails loud instead of silently ignoring HARNESS_SANDBOX=docker
- /delete removes the session's on-disk workspace, not just the DB row
- usage_for_user attaches the right last-task per session in one query

No key, no network.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import interfaces.cli as cli
import interfaces.pipeline_cli as pcli
from config import Config
from observability.usage_store import PersistentUsageTracker, usage_for_user
from providers.base import Usage
from storage.db import make_engine
from storage.user_store import DbUserStore


def test_pipeline_fails_loud_on_sandbox_docker():
    """HARNESS_SANDBOX=docker must not silently run unsandboxed in the
    autonomous pipeline -- it must refuse to start."""
    os.environ["HARNESS_SANDBOX"] = "docker"
    old_argv = sys.argv
    sys.argv = ["pipeline.py", "do a thing"]
    try:
        pcli.main()
    except SystemExit as exc:
        assert "not supported by the autonomous pipeline" in str(exc), exc
    else:
        raise AssertionError("pipeline must raise SystemExit when HARNESS_SANDBOX=docker")
    finally:
        sys.argv = old_argv
        del os.environ["HARNESS_SANDBOX"]
    print("  pipeline fails loud on HARNESS_SANDBOX=docker (no silent host run) OK")


def test_delete_removes_workspace_directory():
    with tempfile.TemporaryDirectory() as tmp:
        workspace_dir = os.path.join(tmp, "workspaces", "alice")
        session_ws = os.path.join(workspace_dir, "20260101-000000")
        os.makedirs(session_ws)
        with open(os.path.join(session_ws, "scratch.txt"), "w") as f:
            f.write("session artifact")

        config = Config(workspace_dir=workspace_dir)
        assert os.path.isdir(session_ws)
        cli._delete_session_workspace(config, "20260101-000000")
        assert not os.path.exists(session_ws), "the session's workspace dir must be gone"

        # A crafted id can't escape workspace_dir (defence in depth).
        outside = os.path.join(tmp, "outside")
        os.makedirs(outside)
        cli._delete_session_workspace(config, "../../outside")
        assert os.path.isdir(outside), "traversal id must not delete anything outside workspace_dir"
    print("  /delete removes the session workspace dir, can't traverse out OK")


def test_usage_for_user_last_task_is_per_session_single_query():
    with tempfile.TemporaryDirectory() as tmp:
        engine = make_engine(f"sqlite:///{os.path.join(tmp, 't.db')}")
        uid = DbUserStore(engine).register("busy", "pw")
        state = {"session": "s1", "task": "task-one"}
        t = PersistentUsageTracker(engine, uid, lambda: state["session"], lambda: state["task"])
        t.record("anthropic/claude-opus-4-8", Usage(10, 5))
        state.update(session="s2", task="task-two")
        t.record("anthropic/claude-opus-4-8", Usage(20, 10))
        state["task"] = "task-two-newer"
        t.record("anthropic/claude-opus-4-8", Usage(1, 1))

        rows = {r["session_id"]: r for r in usage_for_user(engine, "busy")}
        assert rows["s1"]["last_task"] == "task-one"
        assert rows["s2"]["last_task"] == "task-two-newer", "must be the most recent task in s2"
    print("  usage_for_user reports the correct per-session last task (one query) OK")


def main():
    test_pipeline_fails_loud_on_sandbox_docker()
    test_delete_removes_workspace_directory()
    test_usage_for_user_last_task_is_per_session_single_query()
    print("FIXES TESTS PASSED")


if __name__ == "__main__":
    main()
